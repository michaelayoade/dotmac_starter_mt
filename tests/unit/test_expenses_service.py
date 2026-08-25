"""ERP parity and ownership canaries for `dotmac-expenses`."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from dotmac_expenses.contracts import (
    AddPolicyRule,
    ApplyDecision,
    ApprovedLineAmount,
    AttachReceipt,
    ClaimLineDraft,
    ClaimStatus,
    Conflict,
    CreateCategory,
    CreateClaim,
    CreateClaimFromRequest,
    CreatePolicyRevision,
    CreateRequest,
    Decision,
    EvaluationResult,
    InvalidLifecycle,
    LimitAction,
    LimitPeriod,
    PolicyContext,
    PolicyTarget,
    RequestLineDraft,
    RequestStatus,
    ReviseClaim,
    ReviseRequest,
)
from dotmac_expenses.models import (
    TENANT_TABLES,
    ExpenseClaim,
    ExpenseLifecycleEvent,
    ExpensePolicyEvaluation,
)
from dotmac_expenses.service import (
    add_policy_rule,
    apply_claim_decision,
    apply_request_decision,
    attach_receipt,
    create_category,
    create_claim,
    create_claim_from_request,
    create_policy_revision,
    create_request,
    publish_policy,
    reimbursement_eligibility,
    resubmit_claim,
    revise_claim,
    revise_request,
    submit_claim,
    submit_request,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Party, PartyPerson, PartyType, Tenant
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()
CLAIMANT = uuid.uuid4()
NOW = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_expenses": None}},
    )
    Tenant.__table__.create(engine)
    Party.__table__.create(engine)
    PartyPerson.__table__.create(engine)
    from dotmac_expenses import models

    for table_name in TENANT_TABLES:
        models.metadata_table(table_name).create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT, slug="alpha", name="Alpha"),
                Tenant(id=OTHER_TENANT, slug="bravo", name="Bravo"),
                Party(
                    id=CLAIMANT,
                    tenant_id=TENANT,
                    party_type=PartyType.person,
                    display_name="Ada Lovelace",
                ),
            ]
        )
        session.flush()
        session.add(
            PartyPerson(
                party_id=CLAIMANT,
                first_name="Ada",
                last_name="Lovelace",
            )
        )
        session.flush()
        yield session
    engine.dispose()


def _scope() -> TenantScope:
    return TenantScope(TENANT)


def _category(
    db: Session,
    *,
    code: str = "fuel",
    requires_receipt: bool = False,
):
    return create_category(
        db,
        scope=_scope(),
        command=CreateCategory(
            code=code,
            name=code.title(),
            requires_receipt=requires_receipt,
        ),
    )


def _claim(db: Session, *, amount: str = "100.00", requires_receipt: bool = False):
    category = _category(db, requires_receipt=requires_receipt)
    claim = create_claim(
        db,
        scope=_scope(),
        command=CreateClaim(
            reference=f"CLM-{uuid.uuid4().hex[:8]}",
            claimant_party_id=CLAIMANT,
            purpose="Generator fuel",
            claim_date=date(2026, 8, 18),
            currency_code="ngn",
            lines=(
                ClaimLineDraft(
                    category_id=category.id,
                    description="Diesel",
                    claimed_amount=Decimal(amount),
                    expense_date=date(2026, 8, 17),
                    vendor_name="Depot",
                ),
            ),
        ),
        actor_reference="person:ada",
        recorded_at=NOW,
    )
    return claim, category


def test_category_code_is_tenant_unique_and_normalized(db: Session) -> None:
    category = _category(db)
    assert category.code == "FUEL"
    with pytest.raises(Conflict, match="category code"):
        _category(db, code=" FUEL ")


def test_request_submission_records_immutable_lifecycle_evidence(db: Session) -> None:
    category = _category(db)
    request = create_request(
        db,
        scope=_scope(),
        command=CreateRequest(
            reference="REQ-001",
            requester_party_id=CLAIMANT,
            purpose="Planned site visit",
            currency_code="ngn",
            needed_by=date(2026, 8, 20),
            lines=(
                RequestLineDraft(
                    category_id=category.id,
                    description="Taxi",
                    amount=Decimal("25.00"),
                    expected_on=date(2026, 8, 20),
                ),
            ),
        ),
        actor_reference="person:ada",
        recorded_at=NOW,
    )
    revise_request(
        db,
        scope=_scope(),
        request_id=request.id,
        command=ReviseRequest(
            purpose="Planned site and depot visit",
            needed_by=date(2026, 8, 21),
            lines=(
                RequestLineDraft(
                    category_id=category.id,
                    description="Taxi and tolls",
                    amount=Decimal("30.00"),
                    expected_on=date(2026, 8, 21),
                ),
            ),
        ),
    )
    outcome = submit_request(
        db,
        scope=_scope(),
        request_id=request.id,
        context=PolicyContext(),
        actor_reference="person:ada",
        submitted_at=NOW,
    )
    assert outcome.blocked is False
    assert request.status == RequestStatus.SUBMITTED
    assert request.total_requested_amount == Decimal("30.00")
    events = list(
        db.scalars(
            select(ExpenseLifecycleEvent).where(
                ExpenseLifecycleEvent.request_id == request.id
            )
        )
    )
    assert [(row.from_status, row.to_status) for row in events] == [
        (None, "draft"),
        ("draft", "submitted"),
    ]


def test_blocking_transaction_limit_refuses_submission_and_keeps_evidence(
    db: Session,
) -> None:
    claim, category = _claim(db, amount="150.00")
    policy = create_policy_revision(
        db,
        scope=_scope(),
        command=CreatePolicyRevision(
            code="standard",
            name="Standard expenses",
            version=1,
            currency_code="NGN",
            effective_from=date(2026, 1, 1),
        ),
    )
    add_policy_rule(
        db,
        scope=_scope(),
        command=AddPolicyRule(
            policy_id=policy.id,
            code="fuel-line-cap",
            name="Fuel line cap",
            target=PolicyTarget.CLAIM,
            period=LimitPeriod.TRANSACTION,
            action=LimitAction.BLOCK,
            limit_amount=Decimal("100.00"),
            category_id=category.id,
        ),
    )
    publish_policy(db, scope=_scope(), policy_id=policy.id, published_at=NOW)

    outcome = submit_claim(
        db,
        scope=_scope(),
        claim_id=claim.id,
        context=PolicyContext(),
        actor_reference="person:ada",
        submitted_at=NOW,
    )
    assert outcome.blocked is True
    assert claim.status == ClaimStatus.DRAFT
    assert outcome.evaluations[0].result == EvaluationResult.BLOCKED
    assert db.scalar(select(func.count()).select_from(ExpensePolicyEvaluation)) == len(
        outcome.evaluations
    )


def test_published_policy_cannot_gain_a_parallel_rule(db: Session) -> None:
    policy = create_policy_revision(
        db,
        scope=_scope(),
        command=CreatePolicyRevision(
            code="standard",
            name="Standard",
            version=1,
            currency_code="NGN",
            effective_from=date(2026, 1, 1),
        ),
    )
    add_policy_rule(
        db,
        scope=_scope(),
        command=AddPolicyRule(
            policy_id=policy.id,
            code="claim-cap",
            name="Claim cap",
            target=PolicyTarget.CLAIM,
            period=LimitPeriod.TRANSACTION,
            action=LimitAction.WARN,
            limit_amount=Decimal("1000.00"),
        ),
    )
    publish_policy(db, scope=_scope(), policy_id=policy.id, published_at=NOW)
    with pytest.raises(InvalidLifecycle, match="published"):
        add_policy_rule(
            db,
            scope=_scope(),
            command=AddPolicyRule(
                policy_id=policy.id,
                code="late-rule",
                name="Late rule",
                target=PolicyTarget.CLAIM,
                period=LimitPeriod.MONTH,
                action=LimitAction.BLOCK,
                limit_amount=Decimal("1.00"),
            ),
        )


def test_receipt_metadata_and_approval_derive_reimbursement_eligibility(
    db: Session,
) -> None:
    claim, _ = _claim(db, requires_receipt=True)
    first = submit_claim(
        db,
        scope=_scope(),
        claim_id=claim.id,
        context=PolicyContext(),
        actor_reference="person:ada",
        submitted_at=NOW,
    )
    assert first.blocked is True
    assert "receipt.required" in {row.reason_code for row in first.evaluations}

    line = claim.lines[0]
    attach_receipt(
        db,
        scope=_scope(),
        command=AttachReceipt(
            claim_line_id=line.id,
            file_id=uuid.uuid4(),
            original_filename="receipt.jpg",
            media_type="image/jpeg",
            size_bytes=4,
            sha256="a" * 64,
            receipt_number="R-10",
            merchant_name="Depot",
            issued_on=date(2026, 8, 17),
            gross_amount=Decimal("100.00"),
            currency_code="NGN",
        ),
        actor_reference="person:ada",
        recorded_at=NOW,
    )
    second = submit_claim(
        db,
        scope=_scope(),
        claim_id=claim.id,
        context=PolicyContext(),
        actor_reference="person:ada",
        submitted_at=NOW,
    )
    assert second.blocked is False
    apply_claim_decision(
        db,
        scope=_scope(),
        claim_id=claim.id,
        command=ApplyDecision(
            decision=Decision.APPROVED,
            decision_reference="approvals:decision-1",
            approved_lines=(
                ApprovedLineAmount(line_id=line.id, amount=Decimal("90.00")),
            ),
        ),
        actor_reference="approvals",
        decided_at=NOW,
    )
    result = reimbursement_eligibility(db, scope=_scope(), claim_id=claim.id)
    assert result.eligible is True
    assert result.reasons == ()
    assert result.approved_amount == Decimal("90.00")
    assert not hasattr(claim, "paid")
    assert not hasattr(claim, "is_eligible")


def test_rejected_claim_can_be_revised_without_erasing_evidence(db: Session) -> None:
    claim, _ = _claim(db)
    submit_claim(
        db,
        scope=_scope(),
        claim_id=claim.id,
        context=PolicyContext(),
        actor_reference="person:ada",
        submitted_at=NOW,
    )
    apply_claim_decision(
        db,
        scope=_scope(),
        claim_id=claim.id,
        command=ApplyDecision(
            decision=Decision.REJECTED,
            decision_reference="approvals:decision-2",
            reason="Receipt unclear",
        ),
        actor_reference="approvals",
        decided_at=NOW,
    )
    resubmit_claim(
        db,
        scope=_scope(),
        claim_id=claim.id,
        actor_reference="person:ada",
        recorded_at=NOW,
    )
    revise_claim(
        db,
        scope=_scope(),
        claim_id=claim.id,
        command=ReviseClaim(
            purpose="Generator fuel with corrected receipt",
            claim_date=date(2026, 8, 18),
            lines=(
                ClaimLineDraft(
                    category_id=claim.lines[0].category_id,
                    description="Corrected diesel quantity",
                    claimed_amount=Decimal("75.00"),
                    expense_date=date(2026, 8, 17),
                ),
            ),
        ),
    )
    assert claim.status == ClaimStatus.DRAFT
    assert claim.decision_reference is None
    assert claim.total_claimed_amount == Decimal("75.00")
    assert claim.lines[0].description == "Corrected diesel quantity"
    assert (
        db.scalar(
            select(func.count())
            .select_from(ExpenseLifecycleEvent)
            .where(ExpenseLifecycleEvent.claim_id == claim.id)
        )
        == 4
    )


def test_approved_request_converts_once_to_a_separate_draft_claim(db: Session) -> None:
    category = _category(db)
    request = create_request(
        db,
        scope=_scope(),
        command=CreateRequest(
            reference="REQ-002",
            requester_party_id=CLAIMANT,
            purpose="Transport",
            currency_code="NGN",
            needed_by=date(2026, 8, 20),
            lines=(
                RequestLineDraft(
                    category_id=category.id,
                    description="Taxi",
                    amount=Decimal("40.00"),
                    expected_on=date(2026, 8, 20),
                ),
            ),
        ),
        actor_reference="person:ada",
        recorded_at=NOW,
    )
    submit_request(
        db,
        scope=_scope(),
        request_id=request.id,
        context=PolicyContext(),
        actor_reference="person:ada",
        submitted_at=NOW,
    )
    apply_request_decision(
        db,
        scope=_scope(),
        request_id=request.id,
        command=ApplyDecision(
            decision=Decision.APPROVED,
            decision_reference="approvals:req-2",
        ),
        actor_reference="approvals",
        decided_at=NOW,
    )
    claim = create_claim_from_request(
        db,
        scope=_scope(),
        command=CreateClaimFromRequest(
            request_id=request.id,
            reference="CLM-002",
            claim_date=date(2026, 8, 20),
        ),
        actor_reference="person:ada",
        recorded_at=NOW,
    )
    assert request.status == RequestStatus.CONVERTED
    assert claim.status == ClaimStatus.DRAFT
    assert claim.request_id == request.id
    assert claim.total_claimed_amount == request.total_requested_amount
    assert claim.lines[0].category_id == request.lines[0].category_id
    with pytest.raises(Conflict, match="already has a claim"):
        create_claim_from_request(
            db,
            scope=_scope(),
            command=CreateClaimFromRequest(
                request_id=request.id,
                reference="CLM-003",
                claim_date=date(2026, 8, 20),
            ),
            actor_reference="person:ada",
            recorded_at=NOW,
        )


def test_scope_never_reads_another_tenants_claim(db: Session) -> None:
    claim, _ = _claim(db)
    from dotmac_expenses.contracts import NotFound

    with pytest.raises(NotFound):
        reimbursement_eligibility(
            db,
            scope=TenantScope(OTHER_TENANT),
            claim_id=claim.id,
        )
    assert db.scalar(select(func.count()).select_from(ExpenseClaim)) == 1
