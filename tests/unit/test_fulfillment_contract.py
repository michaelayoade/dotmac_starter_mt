"""Contract and lifecycle canaries for reusable fulfillment."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_fulfillment import (
    AttemptRequest,
    CompensationCommand,
    CompensationDisposition,
    CompensationOutcome,
    CompensationRequest,
    FulfillmentConflict,
    OutcomeClass,
    OutcomeMessage,
    ParticipantRegistry,
    RepairAction,
    RepairActor,
    RunCreate,
    RunProgress,
    StaleOutcome,
    StepDefinition,
    create_run,
    derive_run_progress,
    list_repair_attention,
    record_compensation_outcome,
    record_outcome,
    record_reviewed_terminal_outcome,
    request_attempt,
    request_compensation,
    request_redrive,
)
from dotmac_fulfillment.manifest import module as fulfillment_module
from dotmac_fulfillment.models import (
    SCHEMA,
    FulfillmentAttempt,
    FulfillmentOutcomeReceipt,
    FulfillmentRun,
)
from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict, IdempotencyRecord
from dotmac_kernel.models import Base
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.providers.provisioning import (
    CompensationDisposition as ProviderCompensationDisposition,
)
from dotmac_kernel.providers.provisioning import (
    ProvisioningOutcomeClass,
    ProvisioningOutcomeEnvelope,
    ProvisioningProvider,
    ProvisioningRequest,
)
from dotmac_kernel.testing import (
    FakeProvisioningProvider,
    check_provisioning_provider_contract,
    create_test_engine,
    isolated_session,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session


# `tests/unit/conftest.py`'s shared engine deliberately attaches only
# `mod_appdir` and `mod_tstudio` — the two module schemas the reference
# assembly's own service tests consume — and says so: "Independent packages
# build their own narrow engines", because SQLite allows ten attachments and
# the package inventory keeps growing. `dotmac-fulfillment` is not composed by
# this assembly, so it takes that route rather than widening the shared set,
# which would read as composition the assembly does not perform.
#
# The selection is every public table plus this package's own — the public half
# is what `tenant_row` needs to grant entitlements and what the service writes
# audit and idempotency rows into; `create_test_engine` derives the single
# ATTACH from the tables it is handed.
@pytest.fixture(scope="module")
def fulfillment_engine():
    engine = create_test_engine(
        tables=tuple(
            table
            for table in Base.metadata.tables.values()
            if table.schema is None or table.schema == SCHEMA
        )
    )
    yield engine
    engine.dispose()


@pytest.fixture()
def db(fulfillment_engine) -> Iterator[Session]:
    """Overrides the conftest fixture of the same name, so `tenant_row` and
    every test in this module resolve to the narrow engine above.
    """
    with isolated_session(fulfillment_engine) as session:
        yield session


def _round_tripped(command: CompensationCommand) -> CompensationCommand:
    """The same command with a naive `requested_at` read back as UTC.

    `request_compensation` returns a command rebuilt from the stored row, while
    the one handed to `publish` is still in memory. The column is TIMESTAMPTZ
    and PostgreSQL returns it aware, so the two are equal there and this is a
    no-op. SQLite has no timezone-aware type and hands the value back naive, so
    on this lane alone they differ by tzinfo and nothing else. Normalizing is
    honest about that; comparing field-by-field would quietly stop checking the
    timestamp at all.
    """
    if command.requested_at.tzinfo is not None:
        return command
    return replace(command, requested_at=command.requested_at.replace(tzinfo=UTC))


def _registry(*codes: str) -> ParticipantRegistry:
    manifests = [
        ModuleManifest(
            code=f"owner_{index}",
            version="1.0.0",
            provisioning_participants=(code,),
        )
        for index, code in enumerate(codes)
    ]
    return ParticipantRegistry.from_manifests(manifests)


def _run_request(*codes: str) -> RunCreate:
    return RunCreate(
        intent_ref="order:42",
        idempotency_key="create-order-42",
        correlation_id="corr-42",
        steps=tuple(
            StepDefinition(
                step_id=f"line-{index}",
                participant_code=code,
                command_type="service.converge.v1",
                spec={"line": index},
                line_ref=f"order-line:{index}",
            )
            for index, code in enumerate(codes, start=1)
        ),
    )


def _attempt(step_id: str, suffix: str) -> AttemptRequest:
    return AttemptRequest(
        step_id=step_id,
        command_id=f"command-{suffix}",
        operation_id=f"operation-{suffix}",
        idempotency_key=f"attempt-{suffix}",
        correlation_id="corr-42",
        causation_id="create-order-42",
        requested_at=datetime(2026, 8, 19, 9, tzinfo=UTC),
        reobserve_at=datetime(2026, 8, 19, 9, 30, tzinfo=UTC),
    )


def _repair_actor() -> RepairActor:
    install_audit_actions(AuditActionRegistry.from_manifests([fulfillment_module]))
    return RepairActor(actor_type="user", actor_id="operator-42", actor_label="Ada")


def _outcome(
    command_id: str,
    operation_id: str,
    participant: str,
    classification: OutcomeClass,
    *,
    suffix: str | None = None,
    reobserve_at: datetime | None = None,
) -> OutcomeMessage:
    return OutcomeMessage(
        outcome_id=f"outcome-{suffix or command_id}",
        participant_code=participant,
        command_id=command_id,
        operation_id=operation_id,
        classification=classification,
        occurred_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
        provider_status="succeeded"
        if classification is OutcomeClass.SUCCEEDED
        else None,
        reason_code=None
        if classification is OutcomeClass.SUCCEEDED
        else "provider_busy",
        reobserve_at=reobserve_at,
    )


def test_kernel_participant_contract_carries_scope_identity_and_async_outcome() -> None:
    tenant_id = uuid4()
    request = ProvisioningRequest(
        participant_code="domain.registration",
        scope=TenantScope(tenant_id),
        intent_id="domain:example.ng",
        spec={"fqdn": "example.ng"},
    )
    provider = FakeProvisioningProvider()

    assert isinstance(provider, ProvisioningProvider)
    applied = provider.apply(request)
    compensated = provider.compensate(applied.operation_id, "order cancelled")
    assert compensated.disposition is ProviderCompensationDisposition.SUCCEEDED

    outcome = ProvisioningOutcomeEnvelope(
        outcome_id="provider-event-1",
        participant_code=request.participant_code,
        scope=request.scope,
        intent_id=request.intent_id,
        operation_id=applied.operation_id,
        classification=ProvisioningOutcomeClass.SUCCEEDED,
        occurred_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
    )
    assert outcome.operation_id == applied.operation_id
    check_provisioning_provider_contract(FakeProvisioningProvider)


def test_participant_codes_are_manifest_owned_and_duplicates_fail_closed() -> None:
    registry = _registry("domain.registration", "hosting.account")
    registry.require("domain.registration")
    with pytest.raises(KeyError, match="not declared"):
        registry.require("radius.push")

    with pytest.raises(ValueError, match="declared by both"):
        ParticipantRegistry.from_manifests(
            [
                ModuleManifest(
                    code="first",
                    version="1",
                    provisioning_participants=("shared",),
                ),
                ModuleManifest(
                    code="second",
                    version="1",
                    provisioning_participants=("shared",),
                ),
            ]
        )


def test_out_of_order_outcomes_are_recorded_but_do_not_skip_an_earlier_step(
    db: Session, tenant_row
) -> None:
    participants = _registry("participant.one", "participant.two")
    run = create_run(
        db,
        tenant_id=tenant_row.id,
        request=_run_request("participant.one", "participant.two"),
        participants=participants,
    )
    published = []
    first = request_attempt(
        db,
        tenant_id=tenant_row.id,
        run_id=run.id,
        request=_attempt("line-1", "one"),
        participants=participants,
        publish=lambda _db, command: published.append(command),
        schedule_reobservation=lambda *_: None,
    )
    second = request_attempt(
        db,
        tenant_id=tenant_row.id,
        run_id=run.id,
        request=_attempt("line-2", "two"),
        participants=participants,
        publish=lambda _db, command: published.append(command),
        schedule_reobservation=lambda *_: None,
    )

    record_outcome(
        db,
        tenant_id=tenant_row.id,
        message=_outcome(
            second.command_id,
            second.operation_id,
            "participant.two",
            OutcomeClass.SUCCEEDED,
        ),
    )
    progress = derive_run_progress(db, tenant_id=tenant_row.id, run_id=run.id)
    assert progress.progress is RunProgress.PARTIAL
    assert progress.settled_prefix == 0
    assert progress.succeeded_step_ids == ("line-2",)

    record_outcome(
        db,
        tenant_id=tenant_row.id,
        message=_outcome(
            first.command_id,
            first.operation_id,
            "participant.one",
            OutcomeClass.SUCCEEDED,
        ),
    )
    progress = derive_run_progress(db, tenant_id=tenant_row.id, run_id=run.id)
    assert progress.progress is RunProgress.SUCCEEDED
    assert progress.settled_prefix == 2
    assert len(published) == 2


def test_outcome_replay_returns_the_original_and_changed_input_conflicts(
    db: Session, tenant_row
) -> None:
    participants = _registry("participant.one")
    run = create_run(
        db,
        tenant_id=tenant_row.id,
        request=_run_request("participant.one"),
        participants=participants,
    )
    dispatched = request_attempt(
        db,
        tenant_id=tenant_row.id,
        run_id=run.id,
        request=_attempt("line-1", "one"),
        participants=participants,
        publish=lambda *_: None,
        schedule_reobservation=lambda *_: None,
    )
    message = _outcome(
        dispatched.command_id,
        dispatched.operation_id,
        "participant.one",
        OutcomeClass.SUCCEEDED,
    )
    first = record_outcome(db, tenant_id=tenant_row.id, message=message)
    replay = record_outcome(db, tenant_id=tenant_row.id, message=message)
    assert replay.receipt_id == first.receipt_id
    assert replay.replayed is True

    changed = OutcomeMessage(
        **{
            **message.as_fingerprint_payload(),
            "outcome_id": message.outcome_id,
            "classification": OutcomeClass.TERMINAL,
            "occurred_at": message.occurred_at,
        }
    )
    with pytest.raises(IdempotencyConflict):
        record_outcome(db, tenant_id=tenant_row.id, message=changed)


def test_failed_enclosing_transaction_rolls_back_run_attempt_and_ledger(
    db: Session, tenant_row
) -> None:
    participants = _registry("participant.rollback")
    before = {
        "runs": db.scalar(select(func.count()).select_from(FulfillmentRun)),
        "attempts": db.scalar(select(func.count()).select_from(FulfillmentAttempt)),
        "idempotency": db.scalar(select(func.count()).select_from(IdempotencyRecord)),
    }

    with pytest.raises(RuntimeError, match="abort consuming transaction"):
        with db.begin_nested():
            run = create_run(
                db,
                tenant_id=tenant_row.id,
                request=_run_request("participant.rollback"),
                participants=participants,
            )
            request_attempt(
                db,
                tenant_id=tenant_row.id,
                run_id=run.id,
                request=_attempt("line-1", "rollback"),
                participants=participants,
                publish=lambda *_: None,
                schedule_reobservation=lambda *_: None,
            )
            raise RuntimeError("abort consuming transaction")

    assert db.scalar(select(func.count()).select_from(FulfillmentRun)) == before["runs"]
    assert (
        db.scalar(select(func.count()).select_from(FulfillmentAttempt))
        == before["attempts"]
    )
    assert (
        db.scalar(select(func.count()).select_from(IdempotencyRecord))
        == before["idempotency"]
    )


def test_lost_callback_converges_from_the_initial_reobservation_schedule(
    db: Session, tenant_row
) -> None:
    participants = _registry("participant.observe")
    schedules = []
    run = create_run(
        db,
        tenant_id=tenant_row.id,
        request=_run_request("participant.observe"),
        participants=participants,
    )
    command = request_attempt(
        db,
        tenant_id=tenant_row.id,
        run_id=run.id,
        request=_attempt("line-1", "callback-lost"),
        participants=participants,
        publish=lambda *_: None,
        schedule_reobservation=lambda _db, schedule: schedules.append(schedule),
    )

    assert len(schedules) == 1
    schedule = schedules[0]
    assert schedule.operation_id == command.operation_id
    assert (
        derive_run_progress(db, tenant_id=tenant_row.id, run_id=run.id).progress
        is RunProgress.IN_PROGRESS
    )

    # The assembly's durable-timer adapter observes the participant and feeds
    # the typed result through the same receipt path as a delivered callback.
    record_outcome(
        db,
        tenant_id=tenant_row.id,
        message=_outcome(
            command.command_id,
            schedule.operation_id,
            schedule.participant_code,
            OutcomeClass.SUCCEEDED,
            suffix="observed-after-callback-loss",
        ),
    )
    assert (
        derive_run_progress(db, tenant_id=tenant_row.id, run_id=run.id).progress
        is RunProgress.SUCCEEDED
    )


def test_retryable_outcome_requires_and_schedules_a_durable_reobservation(
    db: Session, tenant_row
) -> None:
    participants = _registry("participant.one")
    schedules = []
    run = create_run(
        db,
        tenant_id=tenant_row.id,
        request=_run_request("participant.one"),
        participants=participants,
    )
    dispatched = request_attempt(
        db,
        tenant_id=tenant_row.id,
        run_id=run.id,
        request=_attempt("line-1", "one"),
        participants=participants,
        publish=lambda *_: None,
        schedule_reobservation=lambda _db, schedule: schedules.append(schedule),
    )
    due = datetime(2026, 8, 19, 11, tzinfo=UTC)
    record_outcome(
        db,
        tenant_id=tenant_row.id,
        message=_outcome(
            dispatched.command_id,
            dispatched.operation_id,
            "participant.one",
            OutcomeClass.RETRYABLE,
            reobserve_at=due,
        ),
        schedule_reobservation=lambda _db, schedule: schedules.append(schedule),
    )
    assert schedules[0].due_at == datetime(2026, 8, 19, 9, 30, tzinfo=UTC)
    assert schedules[1].due_at == due
    assert schedules[1].attempt_id == dispatched.attempt_id


def test_stale_outcome_for_a_superseded_attempt_is_refused(
    db: Session, tenant_row
) -> None:
    participants = _registry("participant.one")
    run = create_run(
        db,
        tenant_id=tenant_row.id,
        request=_run_request("participant.one"),
        participants=participants,
    )
    first = request_attempt(
        db,
        tenant_id=tenant_row.id,
        run_id=run.id,
        request=_attempt("line-1", "one"),
        participants=participants,
        publish=lambda *_: None,
        schedule_reobservation=lambda *_: None,
    )
    record_outcome(
        db,
        tenant_id=tenant_row.id,
        message=_outcome(
            first.command_id,
            first.operation_id,
            "participant.one",
            OutcomeClass.RETRYABLE,
            reobserve_at=datetime(2026, 8, 19, 11, tzinfo=UTC),
        ),
        schedule_reobservation=lambda *_: None,
    )
    request_redrive(
        db,
        tenant_id=tenant_row.id,
        run_id=run.id,
        request=_attempt("line-1", "two"),
        participants=participants,
        publish=lambda *_: None,
        schedule_reobservation=lambda *_: None,
        actor=_repair_actor(),
        authorize=lambda *_: None,
    )

    with pytest.raises(StaleOutcome):
        record_outcome(
            db,
            tenant_id=tenant_row.id,
            message=_outcome(
                first.command_id,
                first.operation_id,
                "participant.one",
                OutcomeClass.SUCCEEDED,
                suffix="late",
            ),
        )


def test_three_way_partial_progress_and_reverse_compensation_with_refusal(
    db: Session, tenant_row
) -> None:
    codes = ("participant.one", "participant.two", "participant.three")
    participants = _registry(*codes)
    run = create_run(
        db,
        tenant_id=tenant_row.id,
        request=_run_request(*codes),
        participants=participants,
    )
    attempts = []
    for index, _code in enumerate(codes, start=1):
        attempts.append(
            request_attempt(
                db,
                tenant_id=tenant_row.id,
                run_id=run.id,
                request=_attempt(f"line-{index}", str(index)),
                participants=participants,
                publish=lambda *_: None,
                schedule_reobservation=lambda *_: None,
            )
        )

    classes = (
        OutcomeClass.SUCCEEDED,
        OutcomeClass.RETRYABLE,
        OutcomeClass.TERMINAL,
    )
    for attempt, code, classification in zip(attempts, codes, classes, strict=True):
        record_outcome(
            db,
            tenant_id=tenant_row.id,
            message=_outcome(
                attempt.command_id,
                attempt.operation_id,
                code,
                classification,
                reobserve_at=(
                    datetime(2026, 8, 19, 12, tzinfo=UTC)
                    if classification is OutcomeClass.RETRYABLE
                    else None
                ),
            ),
            schedule_reobservation=lambda *_: None,
        )

    progress = derive_run_progress(db, tenant_id=tenant_row.id, run_id=run.id)
    assert progress.progress is RunProgress.PARTIAL
    assert progress.retryable_step_ids == ("line-2",)
    assert progress.terminal_step_ids == ("line-3",)

    commands = []
    compensation = request_compensation(
        db,
        tenant_id=tenant_row.id,
        run_id=run.id,
        request=CompensationRequest(
            idempotency_key="compensate-order-42",
            reason="commercial intent cancelled",
            requested_at=datetime(2026, 8, 19, 13, tzinfo=UTC),
        ),
        publish=lambda _db, command: commands.append(command),
        actor=_repair_actor(),
        authorize=lambda *_: None,
    )
    assert compensation.step_id == "line-1"
    assert commands == [_round_tripped(compensation)]

    recorded = record_compensation_outcome(
        db,
        tenant_id=tenant_row.id,
        outcome=CompensationOutcome(
            outcome_id="comp-outcome-1",
            command_id=compensation.command_id,
            participant_code=compensation.participant_code,
            disposition=CompensationDisposition.MANUAL_REQUIRED,
            occurred_at=datetime(2026, 8, 19, 14, tzinfo=UTC),
            reason_code="registrar_lock",
        ),
    )
    assert recorded.disposition is CompensationDisposition.MANUAL_REQUIRED


def test_compensation_runs_in_reverse_settlement_order_and_replays_once(
    db: Session, tenant_row
) -> None:
    codes = ("participant.one", "participant.two")
    participants = _registry(*codes)
    run = create_run(
        db,
        tenant_id=tenant_row.id,
        request=_run_request(*codes),
        participants=participants,
    )
    attempts = [
        request_attempt(
            db,
            tenant_id=tenant_row.id,
            run_id=run.id,
            request=_attempt(f"line-{index}", f"settled-{index}"),
            participants=participants,
            publish=lambda *_: None,
            schedule_reobservation=lambda *_: None,
        )
        for index in (1, 2)
    ]
    for attempt, code in zip(attempts, codes, strict=True):
        record_outcome(
            db,
            tenant_id=tenant_row.id,
            message=_outcome(
                attempt.command_id,
                attempt.operation_id,
                code,
                OutcomeClass.SUCCEEDED,
            ),
        )

    actor = _repair_actor()
    published = []
    first_request = CompensationRequest(
        idempotency_key="compensate-settled-1",
        reason="unwind completed effects",
        requested_at=datetime(2026, 8, 19, 13, tzinfo=UTC),
    )
    first = request_compensation(
        db,
        tenant_id=tenant_row.id,
        run_id=run.id,
        request=first_request,
        publish=lambda _db, command: published.append(command),
        actor=actor,
        authorize=lambda *_: None,
    )
    assert first.step_id == "line-2"
    record_compensation_outcome(
        db,
        tenant_id=tenant_row.id,
        outcome=CompensationOutcome(
            outcome_id="comp-settled-2",
            command_id=first.command_id,
            participant_code=first.participant_code,
            disposition=CompensationDisposition.SUCCEEDED,
            occurred_at=datetime(2026, 8, 19, 14, tzinfo=UTC),
        ),
    )

    second_request = CompensationRequest(
        idempotency_key="compensate-settled-2",
        reason="unwind completed effects",
        requested_at=datetime(2026, 8, 19, 15, tzinfo=UTC),
    )
    second = request_compensation(
        db,
        tenant_id=tenant_row.id,
        run_id=run.id,
        request=second_request,
        publish=lambda _db, command: published.append(command),
        actor=actor,
        authorize=lambda *_: None,
    )
    replay = request_compensation(
        db,
        tenant_id=tenant_row.id,
        run_id=run.id,
        request=second_request,
        publish=lambda _db, command: published.append(command),
        actor=actor,
        authorize=lambda *_: None,
    )
    assert second.step_id == replay.step_id == "line-1"
    assert len(published) == 2


def test_repair_queue_and_reviewed_terminal_outcome_are_derived_authorized_and_audited(
    db: Session, tenant_row
) -> None:
    participants = _registry("participant.one")
    run = create_run(
        db,
        tenant_id=tenant_row.id,
        request=_run_request("participant.one"),
        participants=participants,
    )
    dispatched = request_attempt(
        db,
        tenant_id=tenant_row.id,
        run_id=run.id,
        request=_attempt("line-1", "review"),
        participants=participants,
        publish=lambda *_: None,
        schedule_reobservation=lambda *_: None,
    )
    attention = list_repair_attention(db, tenant_id=tenant_row.id, run_id=run.id)
    assert len(attention) == 1
    assert attention[0].classification is None

    actor = _repair_actor()
    authorized: list[RepairAction] = []
    record = record_reviewed_terminal_outcome(
        db,
        tenant_id=tenant_row.id,
        message=_outcome(
            dispatched.command_id,
            dispatched.operation_id,
            "participant.one",
            OutcomeClass.TERMINAL,
            suffix="reviewed",
        ),
        actor=actor,
        authorize=lambda _db, _tenant, _actor, action: authorized.append(action),
    )

    assert authorized == [RepairAction.RECORD_TERMINAL_OUTCOME]
    assert list_repair_attention(db, tenant_id=tenant_row.id, run_id=run.id) == ()
    receipt = db.get(FulfillmentOutcomeReceipt, record.receipt_id)
    assert receipt is not None
    assert receipt.reviewed_by_type == "user"
    assert receipt.reviewed_by_id == "operator-42"
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.tenant_id == tenant_row.id,
            AuditEvent.action == RepairAction.RECORD_TERMINAL_OUTCOME.value,
        )
    )
    assert audit is not None
    assert audit.actor_id == "operator-42"

    with pytest.raises(FulfillmentConflict, match="only retryable"):
        request_redrive(
            db,
            tenant_id=tenant_row.id,
            run_id=run.id,
            request=_attempt("line-1", "after-terminal"),
            participants=participants,
            publish=lambda *_: None,
            schedule_reobservation=lambda *_: None,
            actor=actor,
            authorize=lambda *_: None,
        )


def test_run_creation_rejects_unknown_participants_and_changed_replay(
    db: Session, tenant_row
) -> None:
    participants = _registry("participant.one")
    with pytest.raises(KeyError, match="not declared"):
        create_run(
            db,
            tenant_id=tenant_row.id,
            request=_run_request("participant.unknown"),
            participants=participants,
        )

    request = _run_request("participant.one")
    create_run(
        db,
        tenant_id=tenant_row.id,
        request=request,
        participants=participants,
    )
    changed = RunCreate(
        intent_ref=request.intent_ref,
        idempotency_key=request.idempotency_key,
        correlation_id=request.correlation_id,
        steps=(
            StepDefinition(
                step_id="changed",
                participant_code="participant.one",
                command_type="service.converge.v1",
            ),
        ),
    )
    with pytest.raises(IdempotencyConflict):
        create_run(
            db,
            tenant_id=tenant_row.id,
            request=changed,
            participants=participants,
        )


def test_a_run_requires_at_least_one_unique_step() -> None:
    duplicate = StepDefinition(
        step_id="same",
        participant_code="participant.one",
        command_type="service.converge.v1",
    )
    with pytest.raises(FulfillmentConflict, match="unique"):
        RunCreate(
            intent_ref="order:42",
            idempotency_key="create-order-42",
            correlation_id="corr-42",
            steps=(duplicate, duplicate),
        )

    with pytest.raises(FulfillmentConflict, match="at least one"):
        RunCreate(
            intent_ref="order:42",
            idempotency_key="create-order-42",
            correlation_id="corr-42",
            steps=(),
        )
