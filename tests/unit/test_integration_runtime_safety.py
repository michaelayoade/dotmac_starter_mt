"""The outbound runtime's safety properties, as canaries.

Three things had to become true before enabling this runtime against a real
provider was a responsible act, and each fails in a way a green dashboard hides:

1. **a misbehaving connector can be stopped, and stopping it destroys nothing.**
   Quarantine halts one installation's dispatch and ingress while leaving every
   queued delivery, lease and retry schedule exactly where it was — and it has a
   stated way out, because a containment state with no exit is a row someone
   eventually edits by hand;
2. **a rate limit is not a failure.** A provider answering 429 asked us to come
   back; treating that as terminal dead-letters work the provider explicitly
   invited us to resend, and hammering through it is how a throttle becomes a
   ban;
3. **the numbers exist.** Queue depth, oldest age, latency, retries and failures
   are derived from the ledgers at read time, under names that do not change.

Every test here has a NEGATIVE half, because each of these is easy to pass for
the wrong reason: a quarantine check that also swallowed a misconfiguration, a
status-code rescue that also rescued `reconciliation_required`, a backpressure
sweep that also delayed an unrelated installation, a "no metrics client" scan
that would pass over a file that had one.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from dotmac_integration import (
    ADMISSION_REASONS,
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    DeliveryAttempt,
    DispatchNotAdmitted,
    DispatchUnavailable,
    ExecutionPolicy,
    InboxReceipt,
    LifecycleError,
    Outcome,
    OutcomeStatus,
    PollingCheckpoint,
    add_binding,
    admit_installation,
    admit_runtime,
    apply_provider_cooldown,
    create_draft,
    dispatch_metrics,
    enable,
    enqueue_delivery,
    invoke,
    module,
    next_state,
    parse_retry_after,
    prepare,
    put_config_revision,
    quarantine,
    release_quarantine,
    retry_delay_seconds,
    set_binding_enabled,
    settle,
    throttle_cooldown_seconds,
)
from dotmac_integration import admission as admission_module
from dotmac_integration import operations as operations_module
from dotmac_integration.conformance import (
    FAKE_CAPABILITY,
    fake_manifest,
    fake_plugin,
    fake_registry,
)
from dotmac_integration.operations import METRIC_NAMES
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.models_platform import PlatformAdmin, PlatformAuditEvent
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

MODULE_MODELS = (
    ConnectorInstallation,
    ConnectorConfigRevision,
    CapabilityBinding,
    InboxReceipt,
    DeliveryAttempt,
    PollingCheckpoint,
)

SECOND_CAPABILITY = "conformance.second.v1"


@pytest.fixture(autouse=True)
def _installed_integration_audit_actions() -> None:
    """`write_platform_audit_event` refuses an undeclared action.

    That refusal is the point of the declaration registry, so the standalone
    module tests install it rather than route around it.
    """
    install_audit_actions(AuditActionRegistry.from_manifests([module]))


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in MODULE_MODELS:
        model.__table__.create(engine)
    # The kernel's ONE platform audit ledger. Created rather than spied on:
    # "quarantine leaves a trail" is a claim about a persisted row.
    PlatformAdmin.__table__.create(engine)
    PlatformAuditEvent.__table__.create(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def registry() -> Any:
    return fake_registry()


def _enabled(
    db: Session, registry: Any, *, name: str = "primary"
) -> tuple[ConnectorInstallation, CapabilityBinding]:
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name=name
    )
    binding = add_binding(
        db, installation, registry=registry, capability_id=FAKE_CAPABILITY
    )
    put_config_revision(db, installation, registry=registry, config={"a": 1})
    enable(db, installation, registry=registry)
    set_binding_enabled(db, installation, binding, registry=registry, enabled=True)
    return installation, binding


def _queued(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
    key: str = "k",
) -> DeliveryAttempt:
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        event_type="e",
        idempotency_key=key,
        payload={},
    )
    return delivery


def _snapshot(db: Session) -> list[tuple[Any, ...]]:
    """Every durable fact about the outbox, ordered — the thing a halt must not
    change. Read as tuples so the comparison is by VALUE and cannot be satisfied
    by two reads of the same mutated ORM object."""
    rows = db.query(DeliveryAttempt).order_by(DeliveryAttempt.idempotency_key).all()
    return [
        (
            row.id,
            row.state,
            row.attempt_count,
            _utc(row.next_attempt_at),
            _utc(row.leased_until),
            row.payload_digest,
            _utc(row.delivered_at),
        )
        for row in rows
    ]


def _utc(moment: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes where PostgreSQL hands back aware ones.

    Normalised in the TEST rather than worked around in the assertion, so a
    comparison never silently becomes `None == None`.
    """
    if moment is None:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _audit_actions(db: Session) -> list[str]:
    return [
        row.action
        for row in db.query(PlatformAuditEvent)
        .order_by(PlatformAuditEvent.created_at, PlatformAuditEvent.action)
        .all()
    ]


# ── Quarantine ──────────────────────────────────────────────────────────────


def test_a_quarantined_installation_does_not_dispatch(
    db: Session, registry: Any
) -> None:
    """The whole point: a connector the platform stopped trusting stops
    consuming the queue. Not "retries more slowly" — stops."""
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)

    quarantine(db, installation, reason="provider abuse report", actor="ops")

    with pytest.raises(DispatchNotAdmitted) as raised:
        prepare(db, delivery, registry=registry)
    assert raised.value.reason == "installation_quarantined"

    db.refresh(delivery)
    assert delivery.state == "pending", "a quarantined installation claimed work"
    assert delivery.leased_until is None
    assert delivery.attempt_count == 0


def test_quarantine_destroys_no_queued_work(db: Session, registry: Any) -> None:
    """Containment, not deletion.

    An installation is quarantined precisely when nobody is sure what it did, so
    the moment containment starts discarding evidence it stops being
    containment. Compared against a full value snapshot rather than a row count:
    a quarantine that silently reset `next_attempt_at` or cleared a lease would
    keep the count identical while destroying the schedule.
    """
    installation, binding = _enabled(db, registry)
    for index in range(3):
        _queued(db, installation, binding, key=f"k{index}")
    before = _snapshot(db)

    quarantine(db, installation, reason="under investigation")
    with pytest.raises(DispatchNotAdmitted):
        prepare(db, db.query(DeliveryAttempt).first(), registry=registry)

    assert _snapshot(db) == before


def test_quarantine_is_not_reported_as_a_misconfiguration(
    db: Session, registry: Any
) -> None:
    """The sensitivity half: the two refusals must stay distinguishable.

    `DispatchUnavailable` means something is broken and someone should be paged.
    Quarantine is a deliberate, reversible operator act. Collapsing them pages
    an on-call engineer every time a connector is contained — and the fastest
    way to make that happen is a check that raises the nearest existing
    exception, which would still make the test above pass.
    """
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)

    quarantine(db, installation, reason="r")
    with pytest.raises(DispatchNotAdmitted):
        prepare(db, delivery, registry=registry)

    # And the negative: a merely DISABLED installation keeps the old answer, so
    # the new admission path did not quietly take over every non-enabled state.
    release_quarantine(db, installation, reason="cleared")
    assert installation.state == "disabled"
    with pytest.raises(DispatchUnavailable) as raised:
        prepare(db, delivery, registry=registry)
    assert not isinstance(
        raised.value, DispatchNotAdmitted
    ), "a disabled installation is a misconfiguration, not an admission refusal"


def test_quarantine_is_scoped_to_the_installation(db: Session, registry: Any) -> None:
    """Per INSTALLATION — every capability it serves, and no other installation.

    Both halves matter and they pull in opposite directions:

    * narrower (per binding) would leave the same credentials and the same
      connector code running on the installation's other bindings, so a
      compromised credential keeps being used;
    * wider (per capability) would stop well-behaved installations of other
      connectors that merely implement the same contract — a fleet outage
      dressed as containment.
    """
    manifest = fake_manifest(capabilities=(FAKE_CAPABILITY, SECOND_CAPABILITY))
    two_capability_registry = fake_registry(plugins=[fake_plugin(manifest_=manifest)])

    suspect, first = _enabled(db, two_capability_registry, name="suspect")
    second = add_binding(
        db, suspect, registry=two_capability_registry, capability_id=SECOND_CAPABILITY
    )
    set_binding_enabled(
        db, suspect, second, registry=two_capability_registry, enabled=True
    )
    healthy, healthy_binding = _enabled(db, two_capability_registry, name="healthy")

    on_first = _queued(db, suspect, first, key="a")
    on_second = _queued(db, suspect, second, key="b")
    elsewhere = _queued(db, healthy, healthy_binding, key="c")

    quarantine(db, suspect, reason="r")

    # EVERY capability of the quarantined installation stops...
    for delivery in (on_first, on_second):
        with pytest.raises(DispatchNotAdmitted):
            prepare(db, delivery, registry=two_capability_registry)

    # ...and the other installation of the SAME connector, serving the SAME
    # capability, is untouched.
    assert prepare(db, elsewhere, registry=two_capability_registry) is not None


def test_releasing_quarantine_lands_in_disabled_not_enabled(
    db: Session, registry: Any
) -> None:
    """Leaving quarantine and being trusted again are two decisions.

    Collapsing them lets a release skip `enable`'s live connection check, so an
    installation could come out of containment and start dispatching on
    credentials nobody re-verified.
    """
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    quarantine(db, installation, reason="r")

    release_quarantine(db, installation, reason="investigated, connector at fault")
    assert installation.state == "disabled"

    # Still not dispatching — now for the ordinary reason.
    with pytest.raises(DispatchUnavailable):
        prepare(db, delivery, registry=registry)

    # And the durable work survived the whole round trip.
    db.refresh(delivery)
    assert delivery.state == "pending"

    enable(db, installation, registry=registry)
    assert prepare(db, delivery, registry=registry) is not None


def test_releasing_something_that_is_not_quarantined_is_refused(
    db: Session, registry: Any
) -> None:
    """The negative: a release aimed at the wrong installation must fail loudly
    rather than turn a healthy integration off."""
    installation, _ = _enabled(db, registry)
    with pytest.raises(LifecycleError):
        release_quarantine(db, installation, reason="oops")
    assert installation.state == "enabled"


def test_both_quarantine_directions_write_a_platform_audit_event(
    db: Session, registry: Any
) -> None:
    """Who stopped trusting this, when, and why — and who decided it could come
    back. Asserted against the PERSISTED row, and against the state the event
    records, because `state_reason` is overwritten by the very next transition.
    """
    installation, _ = _enabled(db, registry)

    quarantine(db, installation, reason="provider abuse report", actor="ops")
    release_quarantine(db, installation, reason="false positive", actor="ops")

    assert _audit_actions(db) == [
        "integration.installation.quarantined",
        "integration.installation.quarantine_released",
    ]
    entered = (
        db.query(PlatformAuditEvent)
        .filter(PlatformAuditEvent.action == "integration.installation.quarantined")
        .one()
    )
    assert entered.details["previous_state"] == "enabled"
    assert entered.details["reason"] == "provider abuse report"
    assert entered.entity_id == str(installation.id)


# ── Kill switch ─────────────────────────────────────────────────────────────


HALTED = ExecutionPolicy(dispatch_enabled=False)


def test_the_kill_switch_halts_every_dispatch(db: Session, registry: Any) -> None:
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)

    with pytest.raises(DispatchNotAdmitted) as raised:
        prepare(db, delivery, registry=registry, policy=HALTED)
    assert raised.value.reason == "dispatch_halted"


def test_the_kill_switch_drops_no_durable_work(db: Session, registry: Any) -> None:
    """Halt, not purge — and provably resumable.

    A kill switch that quietly dequeued, dead-lettered or rescheduled would look
    identical from the dispatcher's side: nothing dispatches either way. So this
    compares the full outbox by value across the halt AND then resumes, because
    "the rows are still there" is only half the claim — the other half is that
    they still run.
    """
    installation, binding = _enabled(db, registry)
    deliveries = [_queued(db, installation, binding, key=f"k{i}") for i in range(3)]
    before = _snapshot(db)

    for delivery in deliveries:
        with pytest.raises(DispatchNotAdmitted):
            prepare(db, delivery, registry=registry, policy=HALTED)

    assert _snapshot(db) == before, "the kill switch changed durable state"

    # Switch back on: the SAME rows dispatch.
    assert prepare(db, deliveries[0], registry=registry) is not None


def test_the_halt_check_cannot_reach_a_database(db: Session, registry: Any) -> None:
    """Enforced by signature, not by comment.

    A halted deployment must not spend a round trip per queued row rediscovering
    that it is halted, and the way to guarantee that is for the check to have no
    session to spend it on.
    """
    parameters = set(inspect.signature(admit_runtime).parameters)
    assert "db" not in parameters
    assert parameters == {"policy"}

    # Sensitivity: the sibling check DOES take a session, so the assertion above
    # is discriminating rather than trivially true of every function here.
    assert "db" in inspect.signature(admit_installation).parameters


def test_every_admission_refusal_names_a_declared_reason(
    db: Session, registry: Any
) -> None:
    """A refusal with an ad-hoc reason cannot be counted or alerted on."""
    installation, _ = _enabled(db, registry)
    quarantine(db, installation, reason="r")

    for decision in (
        admit_runtime(HALTED),
        admit_installation(db, installation),
    ):
        assert not decision.admitted
        assert decision.reason in ADMISSION_REASONS

    with pytest.raises(ValueError):
        admission_module.AdmissionDecision(admitted=False, reason="made_up_reason")


# ── Provider rate limits ────────────────────────────────────────────────────


def test_a_429_is_retryable_not_terminal() -> None:
    """The headline. A connector may report a rate limit however it likes; the
    engine must not dead-letter work the provider asked us to resend."""
    throttled = Outcome(
        status=OutcomeStatus.TERMINAL,
        error_code="rate_limited",
        provider_status_code=429,
    )
    assert next_state(throttled, attempt_count=1) == "retryable"


def test_a_rescued_rate_limit_still_dead_letters_at_the_cap() -> None:
    """The rescue is not immortality.

    Attempt exhaustion still applies afterwards, so a provider that throttles
    forever eventually stops consuming worker time instead of retrying until
    someone notices the queue.
    """
    throttled = Outcome(status=OutcomeStatus.TERMINAL, provider_status_code=429)
    policy = ExecutionPolicy(max_attempts=3)
    assert next_state(throttled, attempt_count=3, policy=policy) == "dead_letter"


def test_a_genuine_terminal_failure_stays_terminal() -> None:
    """Sensitivity. A rescue that fired on every terminal outcome would make the
    test above pass while making dead-lettering impossible — so a 400 (the
    payload is wrong; resending changes nothing) must still dead-letter."""
    rejected = Outcome(
        status=OutcomeStatus.TERMINAL,
        error_code="invalid_payload",
        provider_status_code=400,
    )
    assert next_state(rejected, attempt_count=1) == "dead_letter"
    # And a terminal outcome that names no status at all is untouched.
    assert next_state(Outcome(status=OutcomeStatus.TERMINAL), attempt_count=1) == (
        "dead_letter"
    )


def test_a_status_code_never_promotes_reconciliation_required() -> None:
    """`reconciliation_required` says the effect MAY HAVE LANDED, which is a
    stronger claim than any status code refutes. Retrying it is how a provider
    performs the same effect twice."""
    uncertain = Outcome(
        status=OutcomeStatus.RECONCILIATION_REQUIRED,
        provider_status_code=429,
    )
    assert next_state(uncertain, attempt_count=1) == "reconciliation_required"


def test_the_providers_own_timing_wins_over_the_curve() -> None:
    """A provider that tells you when to come back knows better than an
    exponential curve, and ignoring it is how rate limits become outages."""
    told = Outcome(
        status=OutcomeStatus.TERMINAL,
        provider_status_code=429,
        retry_after_seconds=17,
    )
    assert retry_delay_seconds(4, told) == 17, "the curve overrode Retry-After"

    # Bounded, though: a provider cannot park a delivery past the ceiling an
    # operator agreed to.
    forever = Outcome(
        status=OutcomeStatus.RETRYABLE, retry_after_seconds=10 * 365 * 24 * 3600
    )
    policy = ExecutionPolicy(max_backoff_seconds=3600)
    assert retry_delay_seconds(1, forever, policy=policy) == 3600


def test_a_throttle_with_no_retry_after_still_waits_the_cooldown() -> None:
    """The configured cooldown is a FLOOR, never a reason to come back early."""
    policy = ExecutionPolicy(base_delay_seconds=1, default_throttle_cooldown_seconds=90)
    silent = Outcome(status=OutcomeStatus.TERMINAL, provider_status_code=429)
    assert retry_delay_seconds(1, silent, policy=policy) == 90
    assert throttle_cooldown_seconds(silent, policy=policy) == 90

    # Negative: a status that is retryable but not a THROTTLE gets no cooldown,
    # so an ordinary 500 does not pause the whole installation.
    transient = Outcome(status=OutcomeStatus.TERMINAL, provider_status_code=500)
    assert throttle_cooldown_seconds(transient, policy=policy) is None


def test_parse_retry_after_reads_both_rfc_forms() -> None:
    """RFC 7231 § 7.1.3 allows delta-seconds and an HTTP-date, and providers use
    both. A connector that handles only the first silently ignores an
    instruction the provider bothered to send."""
    now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
    assert parse_retry_after("120") == 120
    assert parse_retry_after(120) == 120
    assert parse_retry_after("Mon, 24 Aug 2026 12:02:00 GMT", now=now) == 120

    # A date already in the past means "come back now", not "come back before
    # now" — a negative delay would schedule into the past.
    assert parse_retry_after("Mon, 24 Aug 2026 11:00:00 GMT", now=now) == 0

    # Untrusted input on an error path: unusable values fall back to the curve
    # rather than failing the whole outcome.
    for junk in (None, "", "   ", "soon", "-", True):
        assert parse_retry_after(junk) is None, junk


# ── Backpressure ────────────────────────────────────────────────────────────


def _settle_with(
    db: Session, registry: Any, delivery: DeliveryAttempt, outcome: Outcome, **kw: Any
) -> DeliveryAttempt:
    policy = kw.pop("policy", ExecutionPolicy())
    prepared = prepare(db, delivery, registry=registry, policy=policy)
    assert prepared is not None
    return settle(db, delivery, outcome, prepared=prepared, policy=policy, **kw)


def test_a_throttle_delays_the_installations_other_queued_deliveries(
    db: Session, registry: Any
) -> None:
    """Backpressure, not just backoff.

    One delivery observing a 429 has learned something about the whole provider
    ACCOUNT. Sending the deliveries queued behind it anyway is how a throttle
    becomes a ban — so they wait, in the database, where the pause survives a
    worker restart.
    """
    installation, binding = _enabled(db, registry)
    hit = _queued(db, installation, binding, key="hit")
    sibling = _queued(db, installation, binding, key="sibling")
    moment = datetime.now(UTC)

    _settle_with(
        db,
        registry,
        hit,
        Outcome(
            status=OutcomeStatus.TERMINAL,
            provider_status_code=429,
            retry_after_seconds=300,
        ),
        now=moment,
    )

    db.refresh(sibling)
    assert sibling.state == "pending", "backpressure changed a sibling's state"
    delayed_until = _utc(sibling.next_attempt_at)
    assert delayed_until is not None
    assert delayed_until >= moment + timedelta(seconds=300)


def test_backpressure_only_ever_delays(db: Session, registry: Any) -> None:
    """The negative that matters most: a cooldown must never pull a backed-off
    delivery FORWARD, or it would quietly undo the retry curve for every row it
    touched."""
    installation, binding = _enabled(db, registry)
    moment = datetime.now(UTC)
    far = moment + timedelta(hours=4)

    patient = _queued(db, installation, binding, key="patient")
    patient.state = "retryable"
    patient.next_attempt_at = far
    db.flush()

    apply_provider_cooldown(
        db, installation_id=installation.id, cooldown_seconds=60, now=moment
    )

    db.refresh(patient)
    assert _utc(patient.next_attempt_at) == far


def test_backpressure_does_not_reach_another_installation(
    db: Session, registry: Any
) -> None:
    """A rate limit belongs to one provider account. Delaying an unrelated
    connector because this one was throttled is a self-inflicted outage."""
    throttled, throttled_binding = _enabled(db, registry, name="throttled")
    other, other_binding = _enabled(db, registry, name="other")
    bystander = _queued(db, other, other_binding, key="bystander")
    _queued(db, throttled, throttled_binding, key="queued")
    before = _utc(bystander.next_attempt_at)

    delayed = apply_provider_cooldown(
        db, installation_id=throttled.id, cooldown_seconds=600
    )

    db.refresh(bystander)
    assert delayed == 1
    assert _utc(bystander.next_attempt_at) == before


def test_backpressure_does_not_touch_settled_or_in_flight_work(
    db: Session, registry: Any
) -> None:
    """A cooldown is a statement about work not yet started.

    Rewriting the schedule of a delivery another worker holds would fight that
    worker's settle; rewriting a dead-lettered one would put it back on a
    dispatcher's due list forever.
    """
    installation, binding = _enabled(db, registry)
    untouchable = {}
    for key, state in (
        ("flight", "in_flight"),
        ("done", "delivered"),
        ("gone", "dead_letter"),
        ("stuck", "reconciliation_required"),
    ):
        row = _queued(db, installation, binding, key=key)
        row.state = state
        row.next_attempt_at = None
        untouchable[key] = row
    db.flush()

    assert (
        apply_provider_cooldown(
            db, installation_id=installation.id, cooldown_seconds=600
        )
        == 0
    )
    for key, row in untouchable.items():
        db.refresh(row)
        assert row.next_attempt_at is None, key


def test_backpressure_can_be_switched_off(db: Session, registry: Any) -> None:
    """It is a knob with a documented default, not a hardcoded behaviour."""
    installation, binding = _enabled(db, registry)
    hit = _queued(db, installation, binding, key="hit")
    sibling = _queued(db, installation, binding, key="sibling")
    before = _utc(sibling.next_attempt_at)

    _settle_with(
        db,
        registry,
        hit,
        Outcome(status=OutcomeStatus.TERMINAL, provider_status_code=429),
        policy=ExecutionPolicy(apply_provider_backpressure=False),
    )

    db.refresh(sibling)
    assert _utc(sibling.next_attempt_at) == before


def test_the_concurrency_ceiling_applies_backpressure_before_the_limit(
    db: Session, registry: Any
) -> None:
    """The backpressure that acts BEFORE a provider complains.

    A queue that has just been unpaused, or a burst from a bulk job, otherwise
    opens as many concurrent provider calls as there are workers — which is how
    an integration earns the rate limit it then has to back off from.
    """
    policy = ExecutionPolicy(max_in_flight_per_installation=1)
    installation, binding = _enabled(db, registry)
    first = _queued(db, installation, binding, key="first")
    second = _queued(db, installation, binding, key="second")

    assert prepare(db, first, registry=registry, policy=policy) is not None

    with pytest.raises(DispatchNotAdmitted) as raised:
        prepare(db, second, registry=registry, policy=policy)
    assert raised.value.reason == "installation_at_concurrency_limit"
    assert raised.value.decision.retry_after_seconds == policy.lease_seconds

    db.refresh(second)
    assert second.state == "pending", "a refused dispatch still claimed the row"


def test_an_expired_lease_does_not_count_towards_the_ceiling(
    db: Session, registry: Any
) -> None:
    """Sensitivity. Counting stranded rows as concurrency would let one dead
    worker throttle an installation until a human noticed — a ceiling that
    tightens itself is worse than no ceiling."""
    policy = ExecutionPolicy(max_in_flight_per_installation=1)
    installation, binding = _enabled(db, registry)
    stranded = _queued(db, installation, binding, key="stranded")
    stranded.state = "in_flight"
    stranded.leased_until = datetime.now(UTC) - timedelta(hours=1)
    db.flush()

    live = _queued(db, installation, binding, key="live")
    assert prepare(db, live, registry=registry, policy=policy) is not None


def test_no_database_session_reaches_the_provider_call() -> None:
    """Enforced by what a caller CANNOT pass.

    A transaction open across a provider call holds row locks for as long as the
    provider takes to answer — thirty seconds, or until a socket timeout — and
    that is what exhausts the pool. So `invoke` has no session parameter, and
    nothing on the backpressure path sleeps: a pause is a `next_attempt_at`, not
    wall-clock time spent holding a connection.
    """
    assert "db" not in inspect.signature(invoke).parameters

    # Sensitivity: the phases that are SUPPOSED to hold a session do, so the
    # assertion above is about `invoke` rather than about every function.
    assert "db" in inspect.signature(prepare).parameters
    assert "db" in inspect.signature(settle).parameters

    source = inspect.getsource(admission_module)
    for banned in ("time.sleep", "asyncio.sleep", "import time"):
        assert banned not in source, (
            f"{banned} on the admission path would hold wall-clock time where a "
            "schedule belongs"
        )


# ── Metrics ─────────────────────────────────────────────────────────────────


def test_metric_names_are_stable_and_language_neutral() -> None:
    """A cross-repository contract. The exporter, the dashboards and the alert
    rules all key on these strings, so a rename that looks like tidying makes a
    production graph read zero forever rather than fail.

    Pinned as literals, on purpose: comparing `METRIC_NAMES` to itself would
    pass through any rename.
    """
    assert METRIC_NAMES == (
        "integration_outbound_queue_depth",
        "integration_outbound_in_flight",
        "integration_outbound_in_flight_expired",
        "integration_outbound_oldest_queued_age_seconds",
        "integration_outbound_dispatch_latency_seconds_max",
        "integration_outbound_dispatch_latency_seconds_mean",
        "integration_outbound_delivered_window",
        "integration_outbound_retry_scheduled",
        "integration_outbound_retries_total",
        "integration_outbound_failed_total",
        "integration_outbound_reconciliation_required",
        "integration_inbound_receipts_unprocessed",
        "integration_connector_installations_quarantined",
    )
    for name in METRIC_NAMES:
        assert name.isascii() and name.islower()
        assert set(name) <= set("abcdefghijklmnopqrstuvwxyz0123456789_"), name
    assert len(set(METRIC_NAMES)) == len(METRIC_NAMES)
    assert set(operations_module.DispatchMetrics().as_metrics()) == set(METRIC_NAMES)


def test_metrics_report_depth_age_latency_retries_and_failures(
    db: Session, registry: Any
) -> None:
    """The five numbers an operator needs, from the ledgers, at read time."""
    installation, binding = _enabled(db, registry)
    moment = datetime.now(UTC)

    queued = _queued(db, installation, binding, key="queued")
    queued.created_at = moment - timedelta(seconds=900)

    retrying = _queued(db, installation, binding, key="retrying")
    retrying.state = "retryable"
    retrying.attempt_count = 3

    failed = _queued(db, installation, binding, key="failed")
    failed.state = "dead_letter"
    failed.attempt_count = 10

    done = _queued(db, installation, binding, key="done")
    done.state = "delivered"
    done.attempt_count = 1
    done.created_at = moment - timedelta(seconds=42)
    done.delivered_at = moment
    db.flush()

    metrics = dispatch_metrics(db, now=moment)

    assert metrics.queue_depth == 2  # pending + retryable
    assert metrics.oldest_queued_age_seconds == pytest.approx(900, abs=2)
    assert metrics.dispatch_latency_seconds_max == pytest.approx(42, abs=2)
    assert metrics.dispatch_latency_seconds_mean == pytest.approx(42, abs=2)
    assert metrics.delivered_window == 1
    assert metrics.retry_scheduled == 1
    # 3 + 10 + 1 attempts over three attempted rows = 11 retries.
    assert metrics.retries_total == 11
    assert metrics.failed_total == 1


def test_a_first_attempt_success_counts_as_zero_retries(
    db: Session, registry: Any
) -> None:
    """Sensitivity for the retry count. Reporting raw `attempt_count` would make
    a perfectly healthy queue show a retry per delivery — a metric that is
    always nonzero is a metric nobody alerts on."""
    installation, binding = _enabled(db, registry)
    row = _queued(db, installation, binding, key="clean")
    row.state = "delivered"
    row.attempt_count = 1
    db.flush()

    assert dispatch_metrics(db).retries_total == 0


def test_metrics_see_quarantine_and_expired_leases(db: Session, registry: Any) -> None:
    """The two facts that otherwise get rediscovered from first principles at
    3am: part of the queue is deliberately not moving, or a worker died holding
    it."""
    installation, binding = _enabled(db, registry)
    stranded = _queued(db, installation, binding, key="stranded")
    stranded.state = "in_flight"
    stranded.leased_until = datetime.now(UTC) - timedelta(hours=1)
    db.flush()

    assert dispatch_metrics(db).installations_quarantined == 0  # negative first
    quarantine(db, installation, reason="r")

    metrics = dispatch_metrics(db)
    assert metrics.installations_quarantined == 1
    assert metrics.in_flight_expired == 1
    assert metrics.in_flight == 0, "an expired lease is not live concurrency"


def test_metrics_are_derived_not_stored(db: Session, registry: Any) -> None:
    """No stored gauge, because a stored gauge is a second writer over facts the
    ledgers already hold — and it drifts the instant a worker dies between the
    delivery update and the counter update.

    Proven by mutating ONLY the ledger and re-reading: a cached or stored figure
    would still report the old number.
    """
    installation, binding = _enabled(db, registry)
    assert dispatch_metrics(db).queue_depth == 0

    _queued(db, installation, binding, key="one")
    assert dispatch_metrics(db).queue_depth == 1

    # No column on the outbox holds a metric; the numbers have nowhere to be
    # stored even if someone wanted to.
    columns = set(DeliveryAttempt.__table__.columns.keys())
    assert not {c for c in columns if "metric" in c or "gauge" in c}


def test_the_module_introduces_no_second_observability_owner() -> None:
    """The metrics seam is the one this package already had: values derived at
    read time and returned, with the composing assembly owning the exporter.

    A client library here would put a second observability owner inside a module
    several assemblies compose, and would make this package depend on whichever
    client the first adopter happened to prefer.
    """
    clients = ("prometheus", "statsd", "datadog", "opentelemetry", "otel")
    for area in (operations_module, admission_module):
        source = inspect.getsource(area).lower()
        for client in clients:
            assert f"import {client}" not in source, (area.__name__, client)

    # Sensitivity: the scan would in fact catch one.
    planted = "import prometheus_client\n"
    assert any(f"import {c}" in planted for c in clients)


# ── Policy validation ───────────────────────────────────────────────────────


def test_a_throttling_status_that_is_not_retryable_is_refused() -> None:
    """The invariant that keeps a rate limit from dead-lettering: a status may
    only pause the whole installation if it can also rescue the delivery that
    observed it. Otherwise the queue backs off politely while the request that
    discovered the limit is thrown away."""
    with pytest.raises(ValueError, match="not in retryable_provider_status_codes"):
        ExecutionPolicy(
            retryable_provider_status_codes=(500,),
            throttling_provider_status_codes=(429,),
        )


def test_a_zero_concurrency_ceiling_is_refused() -> None:
    """It would refuse every dispatch while reading as a tuning value. The way
    to stop dispatching is `dispatch_enabled=False`, which says so."""
    with pytest.raises(ValueError, match="max_in_flight_per_installation"):
        ExecutionPolicy(max_in_flight_per_installation=0)


def test_the_added_defaults_reproduce_the_previous_behaviour() -> None:
    """Nothing added here is prod-unsafe, and that is a property worth pinning:
    an assembly that upgrades without touching its policy must get exactly what
    it had."""
    policy = ExecutionPolicy()
    assert policy.dispatch_enabled is True
    assert policy.max_in_flight_per_installation is None
    assert policy.apply_provider_backpressure is True
    assert 429 in policy.throttling_provider_status_codes
    assert set(policy.throttling_provider_status_codes) <= set(
        policy.retryable_provider_status_codes
    )
