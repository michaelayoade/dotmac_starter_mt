"""Product-first sales behavior through immutable Quote acceptance."""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, cast

import pytest
from dotmac_kernel.idempotency_models import IdempotencyRecord
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from dotmac_sales import (
        AcceptedQuoteImmutable,
        AcceptQuoteCommand,
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

TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()
ACTOR = cast("SalesActorRef", None)
SUBJECT = cast("SalesSubjectRef", None)


@pytest.fixture(scope="module", autouse=True)
def _load_sales_after_reference_engine(unit_engine: object) -> Iterator[None]:
    """Importing an optional package is not composing it into Starter.

    The shared fixture still uses the released kernel harness, which discovers
    the ten module schemas present when it is built.  Load this candidate only
    after that fixture exists so the sales tests cannot make unrelated Starter
    tests pretend they compose an eleventh package.  The active Timer workstream
    owns the generic explicit-composition harness change.
    """
    del unit_engine
    from dotmac_kernel.models import Base

    baseline_tables = set(Base.metadata.tables)
    import dotmac_sales as sales
    from dotmac_sales import models

    public_names = (
        "AcceptedQuoteImmutable",
        "AcceptQuoteCommand",
        "AuthorQuoteCommand",
        "CaptureLeadOriginCommand",
        "ChangeQuoteDiscountCommand",
        "CreateLeadCommand",
        "CreatePipelineCommand",
        "CreateStageCommand",
        "DiscountInput",
        "DiscountType",
        "LeadStatus",
        "QuoteLineDraft",
        "QuoteStatus",
        "SalesActorRef",
        "SalesActorSnapshot",
        "SalesSubjectRef",
        "SalesSubjectSnapshot",
        "accept_quote",
        "author_quote",
        "capture_lead_origin",
        "change_quote_discount",
        "create_lead",
        "create_pipeline",
        "create_stage",
        "transition_lead",
        "transition_quote",
        "update_quote",
    )
    model_names = (
        "Lead",
        "LeadOrigin",
        "Pipeline",
        "PipelineStage",
        "Quote",
        "QuoteDiscountRevision",
        "QuoteLine",
    )
    globals().update({name: getattr(sales, name) for name in public_names})
    globals().update({name: getattr(models, name) for name in model_names})
    globals()["ACTOR"] = sales.SalesActorRef("staff", "staff-7")
    globals()["SUBJECT"] = sales.SalesSubjectRef("party", "party-9", "3")
    sales_tables = {
        key
        for key, table in Base.metadata.tables.items()
        if key not in baseline_tables and table.schema == "mod_sales"
    }
    assert sales_tables == {
        f"mod_sales.{name}"
        for name in (
            "pipelines",
            "pipeline_stages",
            "leads",
            "lead_origins",
            "quotes",
            "quote_lines",
            "quote_discount_revisions",
        )
    }
    try:
        yield
    finally:
        # The candidate is tested with its own engine below. Restore global
        # kernel metadata so later legacy harness tests do not infer that the
        # reference assembly composes this optional module. The Timer-owned
        # explicit-composition harness will remove the need for this isolation.
        for key in sales_tables:
            Base.metadata.remove(Base.metadata.tables[key])


class FixedClock:
    instant = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.instant


class ActorPort:
    def require_actor(
        self, *, tenant_id: uuid.UUID, actor: SalesActorRef
    ) -> SalesActorSnapshot:
        if tenant_id != TENANT or actor != ACTOR:
            raise ValueError("unknown actor")
        return SalesActorSnapshot(actor, "Ada")


class SubjectPort:
    def require_subject(
        self, *, tenant_id: uuid.UUID, subject: SalesSubjectRef
    ) -> SalesSubjectSnapshot:
        if tenant_id != TENANT or subject != SUBJECT:
            raise ValueError("unknown subject")
        return SalesSubjectSnapshot(subject, "Prospect")


class RecordingOutput:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[tuple[uuid.UUID, str, Mapping[str, object]]] = []

    def stage(
        self,
        db: object,
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
    pipeline = create_pipeline(db, CreatePipelineCommand(TENANT, "Direct Sales"))
    stage = create_stage(
        db, CreateStageCommand(TENANT, pipeline.id, "Qualified", 20, 70)
    )
    return create_lead(
        db,
        CreateLeadCommand(
            TENANT,
            SUBJECT,
            "Abuja branch",
            pipeline.id,
            stage.id,
            "NGN",
            Decimal("125000.00"),
        ),
        subject_port=SubjectPort(),
    )


def _author(db: Session, *, expires_at: datetime | None = None) -> Quote:
    lead = _lead(db)
    return author_quote(
        db,
        AuthorQuoteCommand(
            tenant_id=TENANT,
            command_id=uuid.uuid4(),
            quote_id=uuid.uuid4(),
            actor=ACTOR,
            lead_id=lead.id,
            status=QuoteStatus.DRAFT,
            currency="ngn",
            expires_at=expires_at,
            lines=(
                QuoteLineDraft(
                    "Dedicated internet",
                    Decimal("2"),
                    Decimal("100.005"),
                    "offer:dia:v4",
                    "price:dia:2026-08-18",
                ),
                QuoteLineDraft("Installation", Decimal("1"), Decimal("50")),
            ),
            discount=DiscountInput(
                DiscountType.PERCENTAGE, Decimal("10"), "launch offer"
            ),
            tax_rate=Decimal("7.5"),
        ),
        actor_port=ActorPort(),
        subject_port=SubjectPort(),
        clock=FixedClock(),
    )


def test_pipeline_stage_and_lead_preserve_sub_lifecycle(db: Session) -> None:
    lead = _lead(db)
    assert (lead.status, lead.probability) == (LeadStatus.NEW.value, 70)
    transition_lead(
        db,
        tenant_id=TENANT,
        lead_id=lead.id,
        to_status=LeadStatus.QUALIFIED,
        clock=FixedClock(),
    )
    assert lead.status == LeadStatus.QUALIFIED.value


def test_origin_is_append_only_and_exact_source_identity_replays(db: Session) -> None:
    lead = _lead(db)
    command = CaptureLeadOriginCommand(
        TENANT,
        lead.id,
        "reviewed_import",
        "legacy.sales-import",
        "batch-18:row-7",
        "row-7",
        FixedClock().now(),
        {"source_revision": "f64946f"},
    )
    assert capture_lead_origin(db, command).id == capture_lead_origin(db, command).id
    assert len(db.scalars(select(LeadOrigin)).all()) == 1


def test_quote_authoring_has_exact_money_and_ordered_lines(db: Session) -> None:
    quote = _author(db)
    assert (quote.currency, quote.subtotal) == ("NGN", Decimal("250.01"))
    assert (quote.discount_amount, quote.tax_total, quote.total) == (
        Decimal("25.00"),
        Decimal("16.88"),
        Decimal("241.89"),
    )
    assert [(line.position, line.description) for line in quote.lines] == [
        (1, "Dedicated internet"),
        (2, "Installation"),
    ]
    assert sum((line.amount for line in quote.lines), Decimal("0")) == quote.total


def test_discount_changes_append_a_revision(db: Session) -> None:
    quote = _author(db)
    outcome = change_quote_discount(
        db,
        ChangeQuoteDiscountCommand(
            TENANT,
            uuid.uuid4(),
            quote.id,
            ACTOR,
            quote.discount_revision,
            DiscountInput(DiscountType.FIXED_AMOUNT, Decimal("20"), "exception"),
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
    command = AcceptQuoteCommand(TENANT, uuid.uuid4(), quote.id, ACTOR, "sales-1")
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
    assert second.replayed and len(output.events) == 1
    payload = output.events[0][2]
    assert payload["schema_version"] == 1
    assert payload["accepted_snapshot_sha256"] == first.accepted_snapshot_sha256
    assert payload["sales_subject"] == {
        "kind": "party",
        "opaque_id": "party-9",
        "version": "3",
    }
    assert {
        "sales_order_id",
        "project_id",
        "work_order_id",
        "invoice_id",
        "subscription_id",
        "subscriber_id",
    }.isdisjoint(payload)


def test_second_accept_command_converges_on_same_output(db: Session) -> None:
    quote = _author(db)
    output = RecordingOutput()
    first = accept_quote(
        db,
        AcceptQuoteCommand(TENANT, uuid.uuid4(), quote.id, ACTOR),
        actor_port=ActorPort(),
        subject_port=SubjectPort(),
        output=output,
        clock=FixedClock(),
    )
    second = accept_quote(
        db,
        AcceptQuoteCommand(TENANT, uuid.uuid4(), quote.id, ACTOR),
        actor_port=ActorPort(),
        subject_port=SubjectPort(),
        output=output,
        clock=FixedClock(),
    )
    assert second.event_id == first.event_id
    assert second.replayed and len(output.events) == 1


def test_accepted_quote_refuses_every_service_mutation(db: Session) -> None:
    quote = _author(db)
    accept_quote(
        db,
        AcceptQuoteCommand(TENANT, uuid.uuid4(), quote.id, ACTOR),
        actor_port=ActorPort(),
        subject_port=SubjectPort(),
        output=RecordingOutput(),
        clock=FixedClock(),
    )
    with pytest.raises(AcceptedQuoteImmutable) as error:
        update_quote(db, tenant_id=TENANT, quote_id=quote.id, notes="changed")
    assert error.value.code == "sales.accepted_quote_immutable"


def test_expired_quote_is_refused_before_output(db: Session) -> None:
    quote = _author(db, expires_at=FixedClock().now() - timedelta(seconds=1))
    output = RecordingOutput()
    with pytest.raises(Exception, match="expired"):
        accept_quote(
            db,
            AcceptQuoteCommand(TENANT, uuid.uuid4(), quote.id, ACTOR),
            actor_port=ActorPort(),
            subject_port=SubjectPort(),
            output=output,
            clock=FixedClock(),
        )
    assert output.events == []


def test_output_failure_rolls_back_with_callers_transaction(db: Session) -> None:
    quote = _author(db)
    db.commit()
    with pytest.raises(RuntimeError, match="output unavailable"):
        accept_quote(
            db,
            AcceptQuoteCommand(TENANT, uuid.uuid4(), quote.id, ACTOR),
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


def test_cross_tenant_ids_are_indistinguishable_from_missing(db: Session) -> None:
    quote = _author(db)
    with pytest.raises(Exception, match="not found"):
        transition_quote(
            db,
            tenant_id=OTHER_TENANT,
            quote_id=quote.id,
            to_status=QuoteStatus.SENT,
            clock=FixedClock(),
        )
