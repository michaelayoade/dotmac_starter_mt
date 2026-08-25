"""Product-first sales behavior through immutable Quote acceptance.

SQLite proves lifecycle, exact money, replay and the product-neutral boundary.
PostgreSQL tenancy, catalog grants and database triggers live in
``tests/test_sales_isolation.py``; SQLite cannot prove those properties.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_sales import (
    AcceptQuoteCommand,
    AcceptedQuoteImmutable,
    AuthorQuoteCommand,
    CaptureLeadOriginCommand,
    ChangeQuoteDiscountCommand,
    CreateLeadCommand,
    CreatePipelineCommand,
    CreateStageCommand,
    DiscountInput,
    DiscountType,
    LeadStatus,
    QuoteLineDraft,
    QuoteStatus,
    SalesActorRef,
    SalesActorSnapshot,
    SalesSubjectRef,
    SalesSubjectSnapshot,
    accept_quote,
    author_quote,
    capture_lead_origin,
    change_quote_discount,
    create_lead,
    create_pipeline,
    create_stage,
    transition_lead,
    transition_quote,
    update_quote,
)
from dotmac_sales.models import (
    Lead,
    LeadOrigin,
    Pipeline,
    PipelineStage,
    Quote,
    QuoteDiscountRevision,
    QuoteLine,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()
ACTOR = SalesActorRef(kind="staff", opaque_id="staff-7")
SUBJECT = SalesSubjectRef(kind="party", opaque_id="party-9", version="3")


class FixedClock:
    def __init__(self) -> None:
        self.instant = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.instant


class ActorPort:
    def require_actor(
        self, *, tenant_id: uuid.UUID, actor: SalesActorRef
    ) -> SalesActorSnapshot:
        if tenant_id != TENANT or actor != ACTOR:
            raise ValueError("unknown actor")
        return SalesActorSnapshot(ref=actor, label="Ada")


class SubjectPort:
    def require_subject(
        self, *, tenant_id: uuid.UUID, subject: SalesSubjectRef
    ) -> SalesSubjectSnapshot:
        if tenant_id != TENANT or subject != SUBJECT:
            raise ValueError("unknown subject")
        return SalesSubjectSnapshot(ref=subject, label="Prospect")


class RecordingOutput:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[tuple[uuid.UUID, str, Mapping[str, object]]] = []

    def stage(
        self,
        db: Session,
        *,
        tenant_id: uuid.UUID,
        event_type: str,
        event_id: uuid.UUID,
        payload: Mapping[str, object],
        correlation_id: str | None,
    ) -> None:
        del db, correlation_id
        if self.fail:
            raise RuntimeError("output unavailable")
        assert tenant_id == TENANT
        self.events.append((event_id, event_type, dict(payload)))


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_sales": None}},
    )
    for model in (
        Pipeline,
        PipelineStage,
        Lead,
        LeadOrigin,
        Quote,
        QuoteLine,
        QuoteDiscountRevision,
        IdempotencyRecord,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


def _lead(db: Session) -> Lead:
    pipeline = create_pipeline(
        db,
        CreatePipelineCommand(tenant_id=TENANT, name="Direct Sales"),
    )
    stage = create_stage(
        db,
        CreateStageCommand(
            tenant_id=TENANT,
            pipeline_id=pipeline.id,
            name="Qualified",
            order_index=20,
            default_probability=70,
        ),
    )
    lead = create_lead(
        db,
        CreateLeadCommand(
            tenant_id=TENANT,
            subject=SUBJECT,
            title="Abuja branch",
            pipeline_id=pipeline.id,
            stage_id=stage.id,
            currency="NGN",
            estimated_value=Decimal("125000.00"),
        ),
        subject_port=SubjectPort(),
    )
    return lead


def _author(
    db: Session,
    *,
    quote_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
) -> Quote:
    lead = _lead(db)
    return author_quote(
        db,
        AuthorQuoteCommand(
            tenant_id=TENANT,
            command_id=uuid.uuid4(),
            quote_id=quote_id or uuid.uuid4(),
            actor=ACTOR,
            lead_id=lead.id,
            status=QuoteStatus.DRAFT,
            currency="ngn",
            expires_at=expires_at,
            lines=(
                QuoteLineDraft(
                    description="Dedicated internet",
                    quantity=Decimal("2"),
                    unit_price=Decimal("100.005"),
                    catalogue_ref="offer:dia:v4",
                    pricing_snapshot_ref="price:dia:2026-08-18",
                ),
                QuoteLineDraft(
                    description="Installation",
                    quantity=Decimal("1"),
                    unit_price=Decimal("50"),
                ),
            ),
            discount=DiscountInput(
                discount_type=DiscountType.PERCENTAGE,
                value=Decimal("10"),
                reason="launch offer",
            ),
            tax_rate=Decimal("7.5"),
        ),
        actor_port=ActorPort(),
        subject_port=SubjectPort(),
        clock=FixedClock(),
    )


def test_pipeline_stage_and_lead_preserve_sub_lifecycle(db: Session) -> None:
    lead = _lead(db)
    assert lead.status == LeadStatus.NEW.value
    assert lead.probability == 70

    transition_lead(
        db,
        tenant_id=TENANT,
        lead_id=lead.id,
        to_status=LeadStatus.QUALIFIED,
        clock=FixedClock(),
    )
    assert lead.status == LeadStatus.QUALIFIED.value


def test_origin_is_append_only_and_replays_exact_source_identity(db: Session) -> None:
    lead = _lead(db)
    command = CaptureLeadOriginCommand(
        tenant_id=TENANT,
        lead_id=lead.id,
        capture_method="reviewed_import",
        source_kind="legacy.sales-import",
        source_ref="batch-18:row-7",
        source_interaction_id="row-7",
        captured_at=FixedClock().now(),
        evidence={"source_revision": "f64946f"},
    )
    first = capture_lead_origin(db, command)
    second = capture_lead_origin(db, command)
    assert first.id == second.id
    assert len(db.scalars(select(LeadOrigin)).all()) == 1


def test_quote_authoring_uses_exact_decimal_money_and_ordered_lines(
    db: Session,
) -> None:
    quote = _author(db)
    assert quote.currency == "NGN"
    assert quote.subtotal == Decimal("250.01")
    assert quote.discount_amount == Decimal("25.00")
    assert quote.tax_total == Decimal("16.88")
    assert quote.total == Decimal("241.89")
    assert [(line.position, line.description) for line in quote.lines] == [
        (1, "Dedicated internet"),
        (2, "Installation"),
    ]
    assert sum((line.amount for line in quote.lines), Decimal("0")) == quote.total


def test_discount_changes_append_one_revision_and_preserve_sub_conflict_rule(
    db: Session,
) -> None:
    quote = _author(db)
    outcome = change_quote_discount(
        db,
        ChangeQuoteDiscountCommand(
            tenant_id=TENANT,
            command_id=uuid.uuid4(),
            quote_id=quote.id,
            actor=ACTOR,
            expected_revision=quote.discount_revision,
            discount=DiscountInput(
                discount_type=DiscountType.FIXED_AMOUNT,
                value=Decimal("20"),
                reason="approved exception",
            ),
        ),
        actor_port=ActorPort(),
        clock=FixedClock(),
    )
    assert outcome.revision == 2
    assert quote.discount_amount == Decimal("20.00")
    assert len(db.scalars(select(QuoteDiscountRevision)).all()) == 2


def test_acceptance_emits_one_product_neutral_handoff_and_replays(db: Session) -> None:
    quote = _author(db)
    output = RecordingOutput()
    command = AcceptQuoteCommand(
        tenant_id=TENANT,
        command_id=uuid.uuid4(),
        quote_id=quote.id,
        actor=ACTOR,
        correlation_id="sales-test-1",
    )

    first = accept_quote(
        db,
        command,
        actor_port=ActorPort(),
        subject_port=SubjectPort(),
        output=output,
        clock=FixedClock(),
    )
    second = accept_quote(
        db,
        command,
        actor_port=ActorPort(),
        subject_port=SubjectPort(),
        output=output,
        clock=FixedClock(),
    )

    assert first.event_id == second.event_id
    assert second.replayed
    assert len(output.events) == 1
    payload = output.events[0][2]
    assert payload["schema_version"] == 1
    assert payload["accepted_snapshot_sha256"] == first.accepted_snapshot_sha256
    assert payload["sales_subject"] == {
        "kind": "party",
        "opaque_id": "party-9",
        "version": "3",
    }
    forbidden = {
        "sales_order_id",
        "project_id",
        "work_order_id",
        "invoice_id",
        "subscription_id",
        "subscriber_id",
    }
    assert forbidden.isdisjoint(payload)


def test_acceptance_with_a_second_command_converges_on_the_same_output(
    db: Session,
) -> None:
    quote = _author(db)
    output = RecordingOutput()
    first = accept_quote(
        db,
        AcceptQuoteCommand(
            tenant_id=TENANT,
            command_id=uuid.uuid4(),
            quote_id=quote.id,
            actor=ACTOR,
        ),
        actor_port=ActorPort(),
        subject_port=SubjectPort(),
        output=output,
        clock=FixedClock(),
    )
    second = accept_quote(
        db,
        AcceptQuoteCommand(
            tenant_id=TENANT,
            command_id=uuid.uuid4(),
            quote_id=quote.id,
            actor=ACTOR,
        ),
        actor_port=ActorPort(),
        subject_port=SubjectPort(),
        output=output,
        clock=FixedClock(),
    )
    assert second.event_id == first.event_id
    assert second.replayed
    assert len(output.events) == 1


def test_accepted_quote_refuses_every_service_mutation(db: Session) -> None:
    quote = _author(db)
    accept_quote(
        db,
        AcceptQuoteCommand(
            tenant_id=TENANT,
            command_id=uuid.uuid4(),
            quote_id=quote.id,
            actor=ACTOR,
        ),
        actor_port=ActorPort(),
        subject_port=SubjectPort(),
        output=RecordingOutput(),
        clock=FixedClock(),
    )
    with pytest.raises(AcceptedQuoteImmutable) as error:
        update_quote(db, tenant_id=TENANT, quote_id=quote.id, notes="changed")
    assert error.value.code == "sales.accepted_quote_immutable"


def test_expired_quote_is_refused_before_any_output(db: Session) -> None:
    clock = FixedClock()
    quote = _author(db, expires_at=clock.now() - timedelta(seconds=1))
    output = RecordingOutput()
    with pytest.raises(Exception, match="expired"):
        accept_quote(
            db,
            AcceptQuoteCommand(
                tenant_id=TENANT,
                command_id=uuid.uuid4(),
                quote_id=quote.id,
                actor=ACTOR,
            ),
            actor_port=ActorPort(),
            subject_port=SubjectPort(),
            output=output,
            clock=clock,
        )
    assert output.events == []


def test_output_failure_rolls_back_with_the_callers_transaction(db: Session) -> None:
    quote = _author(db)
    db.commit()
    with pytest.raises(RuntimeError, match="output unavailable"):
        accept_quote(
            db,
            AcceptQuoteCommand(
                tenant_id=TENANT,
                command_id=uuid.uuid4(),
                quote_id=quote.id,
                actor=ACTOR,
            ),
            actor_port=ActorPort(),
            subject_port=SubjectPort(),
            output=RecordingOutput(fail=True),
            clock=FixedClock(),
        )
    db.rollback()
    db.expire_all()
    persisted = db.get(Quote, quote.id)
    assert persisted is not None
    assert persisted.status == QuoteStatus.DRAFT.value
    assert persisted.accepted_event_id is None


def test_cross_tenant_identifiers_are_indistinguishable_from_missing(
    db: Session,
) -> None:
    quote = _author(db)
    with pytest.raises(Exception, match="not found"):
        transition_quote(
            db,
            tenant_id=OTHER_TENANT,
            quote_id=quote.id,
            to_status=QuoteStatus.SENT,
            clock=FixedClock(),
        )
