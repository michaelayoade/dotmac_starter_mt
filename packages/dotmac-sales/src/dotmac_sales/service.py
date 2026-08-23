"""Sales lifecycle through the immutable accepted-Quote boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_sales.contracts import (
    AcceptedQuoteHandoffV1,
    AcceptedQuoteImmutable,
    AcceptedQuoteLineV1,
    AcceptedQuoteTaxComponentV1,
    AcceptQuoteCommand,
    ActorPort,
    AuthorQuoteCommand,
    CaptureLeadOriginCommand,
    ChangeQuoteDiscountCommand,
    ChangeQuoteDiscountOutcome,
    Clock,
    CreateLeadCommand,
    CreatePipelineCommand,
    CreateStageCommand,
    DiscountAction,
    DiscountInput,
    DiscountType,
    InvalidSalesTransition,
    LeadStatus,
    OwnerOutputPort,
    QuoteAcceptanceOutcome,
    QuoteLineDraft,
    QuoteStatus,
    QuoteTermsSnapshotV1,
    QuoteTermValueV1,
    SalesActorSnapshot,
    SalesConflict,
    SalesNotFound,
    SalesSubjectRef,
    SubjectPort,
    canonical_digest,
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

ACCEPTED_QUOTE_EVENT_V1 = "sales.accepted-quote.v1"
ACCEPT_QUOTE_SCOPE_V1 = "sales.accept-quote.v1"

_LEAD_TRANSITIONS = {
    LeadStatus.NEW: frozenset(
        {LeadStatus.CONTACTED, LeadStatus.QUALIFIED, LeadStatus.LOST}
    ),
    LeadStatus.CONTACTED: frozenset({LeadStatus.QUALIFIED, LeadStatus.LOST}),
    LeadStatus.QUALIFIED: frozenset({LeadStatus.PROPOSAL, LeadStatus.LOST}),
    LeadStatus.PROPOSAL: frozenset(
        {LeadStatus.NEGOTIATION, LeadStatus.WON, LeadStatus.LOST}
    ),
    LeadStatus.NEGOTIATION: frozenset({LeadStatus.WON, LeadStatus.LOST}),
    LeadStatus.WON: frozenset(),
    LeadStatus.LOST: frozenset(),
}
_QUOTE_TRANSITIONS = {
    QuoteStatus.DRAFT: frozenset(
        {QuoteStatus.SENT, QuoteStatus.REJECTED, QuoteStatus.EXPIRED}
    ),
    QuoteStatus.SENT: frozenset({QuoteStatus.REJECTED, QuoteStatus.EXPIRED}),
    QuoteStatus.ACCEPTED: frozenset(),
    QuoteStatus.REJECTED: frozenset(),
    QuoteStatus.EXPIRED: frozenset(),
}


class KernelOutboxOutput:
    """Default delivery adapter over the kernel transactional outbox."""

    def stage(
        self,
        db: object,
        *,
        tenant_id: UUID,
        event_type: str,
        event_id: UUID,
        payload: Mapping[str, object],
        correlation_id: str | None,
    ) -> None:
        # Importing the messaging package initializes its inbox adapter, which
        # imports the database-bound idempotency service.  Output delivery is the
        # only path that needs these models, so preserve DB-free package import.
        from dotmac_kernel.messaging.models import OutboxEvent, OutboxStatus

        if not isinstance(db, Session):
            raise TypeError("KernelOutboxOutput requires a SQLAlchemy Session")
        db.add(
            OutboxEvent(
                id=event_id,
                tenant_id=tenant_id,
                event_type=event_type,
                payload=dict(payload),
                status=OutboxStatus.PENDING.value,
                attempts=0,
                correlation_id=correlation_id,
            )
        )
        db.flush()


def create_pipeline(db: Session, command: CreatePipelineCommand) -> Pipeline:
    pipeline = Pipeline(
        tenant_id=command.tenant_id,
        name=_required(command.name, "pipeline name"),
        description=_optional_text(command.description),
        is_active=True,
    )
    db.add(pipeline)
    db.flush()
    return pipeline


def create_stage(db: Session, command: CreateStageCommand) -> PipelineStage:
    _pipeline(db, tenant_id=command.tenant_id, pipeline_id=command.pipeline_id)
    _probability(command.default_probability)
    if command.order_index < 0:
        raise ValueError("stage order_index cannot be negative")
    stage = PipelineStage(
        tenant_id=command.tenant_id,
        pipeline_id=command.pipeline_id,
        name=_required(command.name, "stage name"),
        order_index=command.order_index,
        default_probability=command.default_probability,
        is_active=True,
    )
    db.add(stage)
    db.flush()
    return stage


def create_lead(
    db: Session, command: CreateLeadCommand, *, subject_port: SubjectPort
) -> Lead:
    _pipeline(db, tenant_id=command.tenant_id, pipeline_id=command.pipeline_id)
    stage = _stage(
        db,
        tenant_id=command.tenant_id,
        pipeline_id=command.pipeline_id,
        stage_id=command.stage_id,
    )
    subject = subject_port.require_subject(
        tenant_id=command.tenant_id, subject=command.subject
    )
    probability = (
        stage.default_probability
        if command.probability is None
        else command.probability
    )
    _probability(probability)
    estimated = (
        None
        if command.estimated_value is None
        else _money(command.estimated_value, minor_units=2)
    )
    if estimated is not None and estimated < 0:
        raise ValueError("estimated value cannot be negative")
    lead = Lead(
        tenant_id=command.tenant_id,
        subject_kind=subject.ref.kind,
        subject_opaque_id=subject.ref.opaque_id,
        subject_version=subject.ref.version,
        subject_label=subject.label,
        title=_required(command.title, "lead title"),
        pipeline_id=command.pipeline_id,
        stage_id=command.stage_id,
        status=LeadStatus.NEW.value,
        probability=probability,
        currency=_currency(command.currency),
        estimated_value=estimated,
        expected_close_at=command.expected_close_date,
        notes=_optional_text(command.notes),
    )
    db.add(lead)
    db.flush()
    return lead


def transition_lead(
    db: Session, *, tenant_id: UUID, lead_id: UUID, to_status: LeadStatus, clock: Clock
) -> Lead:
    lead = _lead(db, tenant_id=tenant_id, lead_id=lead_id, lock=True)
    current = LeadStatus(lead.status)
    if to_status == current:
        return lead
    if to_status not in _LEAD_TRANSITIONS[current]:
        raise InvalidSalesTransition(
            f"lead cannot transition from {current.value} to {to_status.value}"
        )
    lead.status = to_status.value
    if to_status is LeadStatus.WON:
        lead.won_at = clock.now()
    elif to_status is LeadStatus.LOST:
        lead.lost_at = clock.now()
    db.flush()
    return lead


def capture_lead_origin(db: Session, command: CaptureLeadOriginCommand) -> LeadOrigin:
    _lead(db, tenant_id=command.tenant_id, lead_id=command.lead_id)
    existing = db.execute(
        select(LeadOrigin).where(
            LeadOrigin.tenant_id == command.tenant_id,
            LeadOrigin.lead_id == command.lead_id,
            LeadOrigin.capture_method == command.capture_method,
            LeadOrigin.source_kind == command.source_kind,
            LeadOrigin.source_ref == command.source_ref,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.source_interaction_id != command.source_interaction_id
            or _comparable(existing.captured_at) != _comparable(command.captured_at)
            or existing.evidence != dict(command.evidence)
        ):
            raise SalesConflict("lead origin identity was reused with new evidence")
        return existing
    origin = LeadOrigin(
        tenant_id=command.tenant_id,
        lead_id=command.lead_id,
        capture_method=_required(command.capture_method, "capture method"),
        source_kind=_required(command.source_kind, "source kind"),
        source_ref=_required(command.source_ref, "source ref"),
        source_interaction_id=_optional_text(command.source_interaction_id),
        captured_at=command.captured_at,
        evidence=dict(command.evidence),
    )
    db.add(origin)
    db.flush()
    return origin


def author_quote(
    db: Session,
    command: AuthorQuoteCommand,
    *,
    actor_port: ActorPort,
    subject_port: SubjectPort,
    clock: Clock,
) -> Quote:
    if command.status not in {QuoteStatus.DRAFT, QuoteStatus.SENT}:
        raise InvalidSalesTransition("a Quote can only be authored as draft or sent")
    if not command.lines:
        raise ValueError("a Quote requires at least one line")
    lead = _lead(db, tenant_id=command.tenant_id, lead_id=command.lead_id)
    subject_port.require_subject(
        tenant_id=command.tenant_id, subject=_subject_ref(lead)
    )
    actor = actor_port.require_actor(tenant_id=command.tenant_id, actor=command.actor)
    duplicate = db.execute(
        select(Quote.id).where(
            Quote.tenant_id == command.tenant_id, Quote.id == command.quote_id
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise SalesConflict(f"Quote {command.quote_id} already exists")
    quote = Quote(
        id=command.quote_id,
        tenant_id=command.tenant_id,
        lead_id=command.lead_id,
        status=command.status.value,
        currency=_currency(command.currency),
        currency_minor_units=_minor_units(command.currency_minor_units),
        subtotal=_money(Decimal("0"), minor_units=command.currency_minor_units),
        discount_type=None,
        discount_value=None,
        discount_amount=_money(Decimal("0"), minor_units=command.currency_minor_units),
        discount_revision=0,
        tax_total=_money(Decimal("0"), minor_units=command.currency_minor_units),
        total=_money(Decimal("0"), minor_units=command.currency_minor_units),
        fulfillment_eligibility_requirement_refs=list(
            _eligibility_requirements(command.fulfillment_eligibility_requirement_refs)
        ),
        expires_at=command.expires_at,
        notes=_optional_text(command.notes),
        authored_by_kind=actor.ref.kind,
        authored_by_opaque_id=actor.ref.opaque_id,
        authored_by_label=actor.label,
        sent_at=clock.now() if command.status is QuoteStatus.SENT else None,
    )
    quote.lines = [
        _new_line(
            command.tenant_id,
            command.quote_id,
            position,
            draft,
            minor_units=command.currency_minor_units,
        )
        for position, draft in enumerate(command.lines, start=1)
    ]
    db.add(quote)
    _apply_discount(quote, command.discount)
    _recalculate(quote)
    if command.discount is not None:
        quote.discount_revision = 1
        quote.discount_revisions.append(
            _discount_revision(
                quote,
                command_id=command.command_id,
                revision=1,
                action=DiscountAction.CREATED,
                discount=command.discount,
                actor=actor,
                changed_at=clock.now(),
            )
        )
    db.flush()
    return quote


def change_quote_discount(
    db: Session,
    command: ChangeQuoteDiscountCommand,
    *,
    actor_port: ActorPort,
    clock: Clock,
) -> ChangeQuoteDiscountOutcome:
    quote = _quote(
        db, tenant_id=command.tenant_id, quote_id=command.quote_id, lock=True
    )
    _mutable(quote)
    prior = db.execute(
        select(QuoteDiscountRevision).where(
            QuoteDiscountRevision.tenant_id == command.tenant_id,
            QuoteDiscountRevision.quote_id == command.quote_id,
            QuoteDiscountRevision.command_id == command.command_id,
        )
    ).scalar_one_or_none()
    if prior is not None:
        return ChangeQuoteDiscountOutcome(
            quote.id, prior.revision, prior.discount_amount, quote.total
        )
    if quote.discount_revision != command.expected_revision:
        raise SalesConflict(
            "discount revision is "
            f"{quote.discount_revision}, expected {command.expected_revision}"
        )
    actor = actor_port.require_actor(tenant_id=command.tenant_id, actor=command.actor)
    _apply_discount(quote, command.discount)
    _recalculate(quote)
    revision = quote.discount_revision + 1
    quote.discount_revision = revision
    quote.discount_revisions.append(
        _discount_revision(
            quote,
            command_id=command.command_id,
            revision=revision,
            action=DiscountAction.REMOVED
            if command.discount is None
            else DiscountAction.CHANGED,
            discount=command.discount,
            actor=actor,
            changed_at=clock.now(),
        )
    )
    db.flush()
    return ChangeQuoteDiscountOutcome(
        quote.id, revision, quote.discount_amount, quote.total
    )


def transition_quote(
    db: Session,
    *,
    tenant_id: UUID,
    quote_id: UUID,
    to_status: QuoteStatus,
    clock: Clock,
) -> Quote:
    quote = _quote(db, tenant_id=tenant_id, quote_id=quote_id, lock=True)
    _mutable(quote)
    if to_status is QuoteStatus.ACCEPTED:
        raise InvalidSalesTransition("Quote acceptance requires accept_quote")
    current = QuoteStatus(quote.status)
    if to_status == current:
        return quote
    if to_status not in _QUOTE_TRANSITIONS[current]:
        raise InvalidSalesTransition(
            f"Quote cannot transition from {current.value} to {to_status.value}"
        )
    quote.status = to_status.value
    now = clock.now()
    if to_status is QuoteStatus.SENT:
        quote.sent_at = now
    elif to_status is QuoteStatus.REJECTED:
        quote.rejected_at = now
    db.flush()
    return quote


def update_quote(
    db: Session, *, tenant_id: UUID, quote_id: UUID, notes: str | None
) -> Quote:
    quote = _quote(db, tenant_id=tenant_id, quote_id=quote_id, lock=True)
    _mutable(quote)
    quote.notes = _optional_text(notes)
    db.flush()
    return quote


def accept_quote(
    db: Session,
    command: AcceptQuoteCommand,
    *,
    actor_port: ActorPort,
    subject_port: SubjectPort,
    output: OwnerOutputPort | None = None,
    clock: Clock,
) -> QuoteAcceptanceOutcome:
    # The idempotency engine imports the kernel transaction adapter.  Keep that
    # import on the execution path so consumers can import the module, inspect
    # its manifest and register its migration lineage without DATABASE_URL.
    from dotmac_kernel.idempotency import execute_once

    owner_output = output or KernelOutboxOutput()

    def operation(session: Session) -> dict[str, object]:
        quote = _quote(
            session, tenant_id=command.tenant_id, quote_id=command.quote_id, lock=True
        )
        if quote.status == QuoteStatus.ACCEPTED.value:
            return _stored_acceptance(quote, quote_replayed=True)
        if quote.status not in {QuoteStatus.DRAFT.value, QuoteStatus.SENT.value}:
            raise InvalidSalesTransition(
                f"Quote in {quote.status!r} state cannot be accepted"
            )
        now = clock.now()
        if quote.expires_at is not None and _comparable(quote.expires_at) < _comparable(
            now
        ):
            raise InvalidSalesTransition("expired Quote cannot be accepted")
        if not quote.lines:
            raise InvalidSalesTransition("Quote without lines cannot be accepted")
        actor = actor_port.require_actor(
            tenant_id=command.tenant_id, actor=command.actor
        )
        subject = subject_port.require_subject(
            tenant_id=command.tenant_id, subject=_subject_ref(quote.lead)
        )
        event_id = uuid4()
        unsigned = _handoff(
            quote,
            event_id=event_id,
            accepted_at=now,
            actor=actor,
            subject_label=subject.label,
            digest="",
        )
        digest_payload = unsigned.as_payload()
        digest_payload.pop("accepted_snapshot_sha256")
        digest = canonical_digest(
            digest_payload, domain="dotmac.sales.accepted-quote.v1"
        )
        handoff = _handoff(
            quote,
            event_id=event_id,
            accepted_at=now,
            actor=actor,
            subject_label=subject.label,
            digest=digest,
        )
        payload = handoff.as_payload()
        quote.status = QuoteStatus.ACCEPTED.value
        quote.accepted_at = now
        quote.accepted_by_kind = actor.ref.kind
        quote.accepted_by_opaque_id = actor.ref.opaque_id
        quote.accepted_by_label = actor.label
        quote.accepted_event_id = event_id
        quote.accepted_snapshot_sha256 = digest
        quote.accepted_handoff = payload
        owner_output.stage(
            session,
            tenant_id=command.tenant_id,
            event_type=ACCEPTED_QUOTE_EVENT_V1,
            event_id=event_id,
            payload=payload,
            correlation_id=command.correlation_id,
        )
        session.flush()
        return _stored_acceptance(quote, quote_replayed=False)

    outcome = execute_once(
        db,
        tenant_id=command.tenant_id,
        scope=ACCEPT_QUOTE_SCOPE_V1,
        key=str(command.command_id),
        fingerprint=canonical_digest(
            asdict(command), domain="dotmac.sales.accept-quote-command.v1"
        ),
        operation=operation,
        operation_name="accept_quote",
        correlation_id=command.correlation_id,
    )
    result = outcome.result
    return QuoteAcceptanceOutcome(
        quote_id=UUID(str(result["quote_id"])),
        event_id=UUID(str(result["event_id"])),
        accepted_at=datetime.fromisoformat(str(result["accepted_at"])),
        accepted_snapshot_sha256=str(result["accepted_snapshot_sha256"]),
        replayed=outcome.replayed or bool(result.get("quote_replayed", False)),
    )


def _pipeline(db: Session, *, tenant_id: UUID, pipeline_id: UUID) -> Pipeline:
    value = db.execute(
        select(Pipeline).where(
            Pipeline.tenant_id == tenant_id, Pipeline.id == pipeline_id
        )
    ).scalar_one_or_none()
    if value is None:
        raise SalesNotFound("pipeline not found")
    return value


def _stage(
    db: Session, *, tenant_id: UUID, pipeline_id: UUID, stage_id: UUID
) -> PipelineStage:
    value = db.execute(
        select(PipelineStage).where(
            PipelineStage.tenant_id == tenant_id,
            PipelineStage.pipeline_id == pipeline_id,
            PipelineStage.id == stage_id,
        )
    ).scalar_one_or_none()
    if value is None:
        raise SalesNotFound("pipeline stage not found")
    return value


def _lead(db: Session, *, tenant_id: UUID, lead_id: UUID, lock: bool = False) -> Lead:
    statement = select(Lead).where(Lead.tenant_id == tenant_id, Lead.id == lead_id)
    value = db.execute(
        statement.with_for_update() if lock else statement
    ).scalar_one_or_none()
    if value is None:
        raise SalesNotFound("lead not found")
    return value


def _quote(
    db: Session, *, tenant_id: UUID, quote_id: UUID, lock: bool = False
) -> Quote:
    statement = select(Quote).where(Quote.tenant_id == tenant_id, Quote.id == quote_id)
    value = db.execute(
        statement.with_for_update() if lock else statement
    ).scalar_one_or_none()
    if value is None:
        raise SalesNotFound("Quote not found")
    return value


def _subject_ref(lead: Lead) -> SalesSubjectRef:
    return SalesSubjectRef(
        lead.subject_kind, lead.subject_opaque_id, lead.subject_version
    )


def _new_line(
    tenant_id: UUID,
    quote_id: UUID,
    position: int,
    draft: QuoteLineDraft,
    *,
    minor_units: int,
) -> QuoteLine:
    if draft.quantity <= 0:
        raise ValueError("Quote line quantity must be positive")
    if draft.unit_price < 0:
        raise ValueError("Quote line unit price cannot be negative")
    gross = _money(draft.quantity * draft.unit_price, minor_units=minor_units)
    terms_ref = _required(draft.terms_ref, "terms ref")
    if draft.terms_snapshot.version_ref != terms_ref:
        raise ValueError("terms snapshot version must match terms_ref")
    terms_values: list[dict[str, str]] = []
    term_names: set[str] = set()
    for term in sorted(draft.terms_snapshot.values, key=lambda item: item.name):
        name = _required(term.name, "term name")
        value = _required(term.value, "term value")
        if name in term_names:
            raise ValueError(f"term {name!r} appears more than once")
        term_names.add(name)
        terms_values.append({"name": name, "value": value})
    if not terms_values:
        raise ValueError("a Quote line requires its accepted terms content")

    tax_rates: list[dict[str, str]] = []
    tax_keys: set[tuple[str, str]] = set()
    for tax in sorted(
        draft.taxes, key=lambda item: (item.tax_code, item.source_version)
    ):
        code = _required(tax.tax_code, "tax code")
        source_version = _required(tax.source_version, "tax source version")
        key = (code, source_version)
        if key in tax_keys:
            raise ValueError(f"tax rate {key!r} appears more than once")
        tax_keys.add(key)
        tax_rates.append(
            {
                "tax_code": code,
                "source_version": source_version,
                "rate": format(_rate(tax.rate, "tax rate"), "f"),
            }
        )
    return QuoteLine(
        tenant_id=tenant_id,
        quote_id=quote_id,
        position=position,
        description=_required(draft.description, "line description"),
        quantity=draft.quantity,
        unit_price=draft.unit_price,
        gross_amount=gross,
        discount_amount=_money(Decimal("0"), minor_units=minor_units),
        tax_amount=_money(Decimal("0"), minor_units=minor_units),
        amount=gross,
        catalogue_ref=_optional_text(draft.catalogue_ref),
        price_version_ref=_required(draft.price_version_ref, "price version ref"),
        terms_ref=terms_ref,
        terms_snapshot={"version_ref": terms_ref, "values": terms_values},
        specification_ref=_required(draft.specification_ref, "specification ref"),
        tax_rates=tax_rates,
        tax_components=[],
    )


def _apply_discount(quote: Quote, discount: DiscountInput | None) -> None:
    if discount is None:
        quote.discount_type = None
        quote.discount_value = None
        return
    if discount.value < 0:
        raise ValueError("discount value cannot be negative")
    if discount.discount_type is DiscountType.PERCENTAGE and discount.value > 100:
        raise ValueError("percentage discount cannot exceed 100")
    quote.discount_type = discount.discount_type.value
    quote.discount_value = discount.value


def _recalculate(quote: Quote) -> None:
    minor_units = quote.currency_minor_units
    gross = [
        _money(line.quantity * line.unit_price, minor_units=minor_units)
        for line in quote.lines
    ]
    subtotal = _money(sum(gross, Decimal("0")), minor_units=minor_units)
    if quote.discount_type is None or quote.discount_value is None:
        discount_total = _money(Decimal("0"), minor_units=minor_units)
    elif quote.discount_type == DiscountType.PERCENTAGE.value:
        discount_total = _money(
            subtotal * quote.discount_value / Decimal("100"),
            minor_units=minor_units,
        )
    else:
        discount_total = _money(quote.discount_value, minor_units=minor_units)
    if discount_total > subtotal:
        raise ValueError("discount cannot exceed Quote subtotal")
    discounts = _allocate(discount_total, gross, minor_units=minor_units)
    taxable = [
        _money(value - reduction, minor_units=minor_units)
        for value, reduction in zip(gross, discounts, strict=False)
    ]
    tax_total = _money(Decimal("0"), minor_units=minor_units)
    for line, line_gross, line_discount, taxable_basis in zip(
        quote.lines, gross, discounts, taxable, strict=False
    ):
        components: list[dict[str, object]] = []
        line_tax = _money(Decimal("0"), minor_units=minor_units)
        for raw in line.tax_rates:
            code = str(raw["tax_code"])
            source_version = str(raw["source_version"])
            rate = Decimal(str(raw["rate"]))
            amount = _money(
                taxable_basis * rate / Decimal("100"),
                minor_units=minor_units,
            )
            line_tax += amount
            components.append(
                {
                    "tax_code": code,
                    "source_version": source_version,
                    "taxable_basis": _money_string(
                        taxable_basis, minor_units=minor_units
                    ),
                    "rate": format(rate, "f"),
                    "amount": _money_string(amount, minor_units=minor_units),
                }
            )
        line.gross_amount = line_gross
        line.discount_amount = line_discount
        line.tax_amount = line_tax
        line.tax_components = components
        line.amount = _money(
            line_gross - line_discount + line_tax, minor_units=minor_units
        )
        tax_total += line_tax
    quote.subtotal = subtotal
    quote.discount_amount = discount_total
    quote.tax_total = tax_total
    quote.total = _money(subtotal - discount_total + tax_total, minor_units=minor_units)


def _allocate(
    total: Decimal, weights: list[Decimal], *, minor_units: int
) -> list[Decimal]:
    if not weights:
        return []
    weight_total = sum(weights, Decimal("0"))
    if total == 0 or weight_total == 0:
        return [_money(Decimal("0"), minor_units=minor_units) for _ in weights]
    values: list[Decimal] = []
    running = _money(Decimal("0"), minor_units=minor_units)
    for index, weight in enumerate(weights):
        share = (
            total - running
            if index == len(weights) - 1
            else _money(total * weight / weight_total, minor_units=minor_units)
        )
        values.append(share)
        running += share
    return values


def _discount_revision(
    quote: Quote,
    *,
    command_id: UUID,
    revision: int,
    action: DiscountAction,
    discount: DiscountInput | None,
    actor: SalesActorSnapshot,
    changed_at: datetime,
) -> QuoteDiscountRevision:
    reason = (
        "discount removed"
        if discount is None
        else _required(discount.reason, "discount reason")
    )
    fingerprint = canonical_digest(
        {
            "quote_id": quote.id,
            "revision": revision,
            "action": action,
            "discount_type": None if discount is None else discount.discount_type,
            "discount_value": None if discount is None else discount.value,
            "discount_amount": quote.discount_amount,
            "reason": reason,
            "actor": asdict(actor.ref),
        },
        domain="dotmac.sales.quote-discount-revision.v1",
    )
    return QuoteDiscountRevision(
        tenant_id=quote.tenant_id,
        quote_id=quote.id,
        command_id=command_id,
        revision=revision,
        action=action.value,
        discount_type=None if discount is None else discount.discount_type.value,
        discount_value=None if discount is None else discount.value,
        discount_amount=quote.discount_amount,
        reason=reason,
        actor_kind=actor.ref.kind,
        actor_opaque_id=actor.ref.opaque_id,
        actor_label=actor.label,
        changed_at=changed_at,
        fingerprint_sha256=fingerprint,
    )


def _handoff(
    quote: Quote,
    *,
    event_id: UUID,
    accepted_at: datetime,
    actor: SalesActorSnapshot,
    subject_label: str,
    digest: str,
) -> AcceptedQuoteHandoffV1:
    return AcceptedQuoteHandoffV1(
        schema_version=1,
        event_id=event_id,
        tenant_id=quote.tenant_id,
        quote_id=quote.id,
        lead_id=quote.lead_id,
        accepted_at=accepted_at,
        accepted_by={"kind": actor.ref.kind, "opaque_id": actor.ref.opaque_id},
        sales_subject={
            "kind": quote.lead.subject_kind,
            "opaque_id": quote.lead.subject_opaque_id,
            "version": quote.lead.subject_version,
        },
        sales_subject_label=subject_label,
        currency=quote.currency,
        currency_minor_units=quote.currency_minor_units,
        subtotal=_money_string(quote.subtotal, minor_units=quote.currency_minor_units),
        discount_amount=_money_string(
            quote.discount_amount, minor_units=quote.currency_minor_units
        ),
        tax_total=_money_string(
            quote.tax_total, minor_units=quote.currency_minor_units
        ),
        total=_money_string(quote.total, minor_units=quote.currency_minor_units),
        lines=tuple(
            AcceptedQuoteLineV1(
                line_id=line.id,
                position=line.position,
                description=line.description,
                quantity=format(line.quantity, "f"),
                unit_price=_money_string(
                    line.unit_price, minor_units=quote.currency_minor_units
                ),
                gross_amount=_money_string(
                    line.gross_amount, minor_units=quote.currency_minor_units
                ),
                discount_amount=_money_string(
                    line.discount_amount, minor_units=quote.currency_minor_units
                ),
                tax_amount=_money_string(
                    line.tax_amount, minor_units=quote.currency_minor_units
                ),
                amount=_money_string(
                    line.amount, minor_units=quote.currency_minor_units
                ),
                catalogue_ref=line.catalogue_ref,
                price_version_ref=line.price_version_ref,
                terms_ref=line.terms_ref,
                terms_snapshot=_stored_terms_snapshot(line.terms_snapshot),
                specification_ref=line.specification_ref,
                taxes=_stored_tax_components(line.tax_components),
            )
            for line in quote.lines
        ),
        fulfillment_eligibility_requirement_refs=tuple(
            quote.fulfillment_eligibility_requirement_refs
        ),
        accepted_snapshot_sha256=digest,
    )


def _stored_terms_snapshot(payload: Mapping[str, object]) -> QuoteTermsSnapshotV1:
    version_ref = payload.get("version_ref")
    raw_values = payload.get("values")
    if not isinstance(version_ref, str) or not isinstance(raw_values, list):
        raise SalesConflict("stored Quote terms snapshot is invalid")
    values: list[QuoteTermValueV1] = []
    for raw in raw_values:
        if not isinstance(raw, Mapping):
            raise SalesConflict("stored Quote terms snapshot is invalid")
        name = raw.get("name")
        value = raw.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            raise SalesConflict("stored Quote terms snapshot is invalid")
        values.append(QuoteTermValueV1(name=name, value=value))
    return QuoteTermsSnapshotV1(version_ref=version_ref, values=tuple(values))


def _stored_tax_components(
    payload: list[dict[str, object]],
) -> tuple[AcceptedQuoteTaxComponentV1, ...]:
    output: list[AcceptedQuoteTaxComponentV1] = []
    for raw in payload:
        tax_code = raw.get("tax_code")
        source_version = raw.get("source_version")
        taxable_basis = raw.get("taxable_basis")
        rate = raw.get("rate")
        amount = raw.get("amount")
        if (
            not isinstance(tax_code, str)
            or not isinstance(source_version, str)
            or not isinstance(taxable_basis, str)
            or (rate is not None and not isinstance(rate, str))
            or not isinstance(amount, str)
        ):
            raise SalesConflict("stored Quote tax component is invalid")
        output.append(
            AcceptedQuoteTaxComponentV1(
                tax_code=tax_code,
                source_version=source_version,
                taxable_basis=taxable_basis,
                rate=rate,
                amount=amount,
            )
        )
    return tuple(output)


def _stored_acceptance(quote: Quote, *, quote_replayed: bool) -> dict[str, object]:
    if (
        quote.accepted_event_id is None
        or quote.accepted_at is None
        or quote.accepted_snapshot_sha256 is None
    ):
        raise SalesConflict("accepted Quote is missing its immutable handoff identity")
    return {
        "quote_id": str(quote.id),
        "event_id": str(quote.accepted_event_id),
        "accepted_at": quote.accepted_at.isoformat(),
        "accepted_snapshot_sha256": quote.accepted_snapshot_sha256,
        "quote_replayed": quote_replayed,
    }


def _mutable(quote: Quote) -> None:
    if quote.status == QuoteStatus.ACCEPTED.value:
        raise AcceptedQuoteImmutable(f"accepted Quote {quote.id} is immutable")


def _money(value: Decimal, *, minor_units: int) -> Decimal:
    quantum = Decimal(1).scaleb(-minor_units)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _money_string(value: Decimal, *, minor_units: int) -> str:
    return format(_money(value, minor_units=minor_units), f".{minor_units}f")


def _rate(value: Decimal, label: str) -> Decimal:
    if isinstance(value, float) or not isinstance(value, Decimal):
        raise ValueError(f"{label} must be an exact Decimal")
    if not value.is_finite() or value < 0 or value > 100:
        raise ValueError(f"{label} must be between 0 and 100")
    return value


def _minor_units(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 6:
        raise ValueError("currency_minor_units must be an integer from zero to six")
    return value


def _eligibility_requirements(values: tuple[str, ...]) -> tuple[str, ...]:
    requirements = tuple(
        sorted(_required(value, "eligibility requirement ref") for value in values)
    )
    if not requirements:
        raise ValueError("a Quote requires a finite fulfillment eligibility set")
    if len(set(requirements)) != len(requirements):
        raise ValueError("fulfillment eligibility requirement refs must be unique")
    return requirements


def _probability(value: int) -> None:
    if value < 0 or value > 100:
        raise ValueError("probability must be between 0 and 100")


def _currency(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isalpha():
        raise ValueError("currency must be a three-letter code")
    return normalized


def _required(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} cannot be blank")
    return normalized


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


def _comparable(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


__all__ = [
    "ACCEPTED_QUOTE_EVENT_V1",
    "ACCEPT_QUOTE_SCOPE_V1",
    "KernelOutboxOutput",
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
]
