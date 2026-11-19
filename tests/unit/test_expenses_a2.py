"""a2: the staff claim liability lifecycle (SQLite unit tier)."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from dotmac_expenses import (
    CLAIM_INPUT_VAT_DEFAULT,
    CLAIM_INPUT_VAT_SETTING,
    INPUT_VAT_IMMEDIATE_ACCOUNT_REF,
    AcceptClaimForReimbursement,
    AcceptedClaimLine,
    ApplyDecision,
    ApprovedLineAmount,
    ClaimAccountingReceiptInput,
    ClaimInputVat,
    ClaimLiabilityStatus,
    ClaimLineDraft,
    ClaimStatus,
    Conflict,
    CreateCategory,
    CreateClaim,
    Decision,
    ExpensesError,
    HistoricalClaimLiabilityCommand,
    HistoricalClaimLiabilityEvidence,
    HistoricalClaimSettlement,
    InvalidCommand,
    InvalidLifecycle,
    LiabilityOrigin,
    NotFound,
    PolicyContext,
    RefusalCode,
    accept_claim_for_reimbursement,
    adjust_claim_liability,
    admit_historical_claim_liability,
    apply_claim_decision,
    build_claim_liability_adjustment_consequence,
    build_claim_liability_consequence,
    build_claim_liability_reversal_consequence,
    build_claim_settlement_consequence,
    build_claim_settlement_reversal_consequence,
    claim_input_vat_spec,
    claim_liability_row_totals,
    claimant_claims_balance,
    claims_payable_total,
    create_category,
    create_claim,
    effective_claim_liability_amount,
    open_claim_liabilities,
    parse_claim_input_vat,
    record_claim_accounting_receipt,
    record_claim_settlement,
    reverse_claim_acceptance,
    reverse_claim_settlement,
    submit_claim,
    withdraw_claim_approval,
)
from dotmac_expenses.models import (
    TENANT_TABLES,
    ClaimAccountingReceipt,
    ClaimLiability,
    ClaimLiabilityAdjustment,
    ClaimLiabilityLine,
    ExpenseCategory,
    ExpenseClaim,
    ExpenseLifecycleEvent,
    HistoricalClaimReceipt,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.models import Party, PartyPerson, PartyType, Tenant
from dotmac_kernel.money import Currency, Money
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, scoped_session, sessionmaker

NGN = Currency("NGN", 2)
TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()
CLAIMANT = uuid.uuid4()
OTHER_CLAIMANT = uuid.uuid4()
NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
LIABILITY_ACCOUNT = "acct:claims-payable"
EXPENSE = "acct:fuel"
EXPENSE_2 = "acct:travel"
OFFSET = "acct:unclaimed"
BANK = "acct:bank-main"
VAT = INPUT_VAT_IMMEDIATE_ACCOUNT_REF


class _Lcg:
    """A deterministic generator (the sequence must reproduce exactly)."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.n = 0

    def randint(self, low: int, high: int) -> int:
        self.n += 1
        digest = hashlib.sha256(f"{self.seed}:{self.n}".encode()).digest()
        return low + int.from_bytes(digest[:8], "big") % (high - low + 1)

    def choice(self, items):
        return items[self.randint(0, len(items) - 1)]


def _later(minutes: int = 5) -> datetime:
    return NOW + timedelta(minutes=minutes)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_expenses": None}},
    )
    Tenant.__table__.create(engine)
    Party.__table__.create(engine)
    PartyPerson.__table__.create(engine)
    IdempotencyRecord.__table__.create(engine)
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
                Party(
                    id=OTHER_CLAIMANT,
                    tenant_id=TENANT,
                    party_type=PartyType.person,
                    display_name="Grace Hopper",
                ),
            ]
        )
        session.flush()
        yield session
    engine.dispose()


def _scope() -> TenantScope:
    return TenantScope(TENANT)


def _key() -> dict[str, object]:
    return {"idempotency_key": uuid.uuid4().hex, "idempotency_expires_at": None}


def _category(db: Session, code: str = "fuel") -> ExpenseCategory:
    return create_category(
        db, scope=_scope(), command=CreateCategory(code=code, name=code.title())
    )


def _approved_claim(
    db: Session,
    *,
    approved: tuple[str, ...] = ("100.00",),
    claimed: tuple[str, ...] | None = None,
    claimant: uuid.UUID = CLAIMANT,
    decide: bool = True,
) -> ExpenseClaim:
    category = _category(db, code=f"c{uuid.uuid4().hex[:6]}")
    claimed = claimed or approved
    claim = create_claim(
        db,
        scope=_scope(),
        command=CreateClaim(
            reference=f"CLM-{uuid.uuid4().hex[:8]}",
            claimant_party_id=claimant,
            purpose="Site visit",
            claim_date=date(2026, 10, 20),
            currency_code="ngn",
            lines=tuple(
                ClaimLineDraft(
                    category_id=category.id,
                    description=f"Line {i}",
                    claimed_amount=Decimal(amount),
                    expense_date=date(2026, 10, 19),
                )
                for i, amount in enumerate(claimed)
            ),
        ),
        actor_reference="person:ada",
        recorded_at=NOW,
    )
    submit_claim(
        db,
        scope=_scope(),
        claim_id=claim.id,
        context=PolicyContext(),
        actor_reference="person:ada",
        submitted_at=NOW,
    )
    if decide:
        apply_claim_decision(
            db,
            scope=_scope(),
            claim_id=claim.id,
            command=ApplyDecision(
                decision=Decision.APPROVED,
                decision_reference=f"approvals:{uuid.uuid4().hex[:6]}",
                approved_lines=tuple(
                    ApprovedLineAmount(line_id=line.id, amount=Decimal(a))
                    for line, a in zip(claim.lines, approved, strict=True)
                ),
            ),
            actor_reference="approvals",
            decided_at=NOW,
        )
    return claim


def _command(
    claim: ExpenseClaim,
    *,
    tax: dict[int, str] | None = None,
    mode: ClaimInputVat = ClaimInputVat.NONE,
    **overrides: object,
) -> AcceptClaimForReimbursement:
    tax = tax or {}
    lines = tuple(
        AcceptedClaimLine(
            claim_line_id=line.id,
            expense_account_ref=EXPENSE if i == 0 else EXPENSE_2,
            dimension_refs=(("project", "p1"),) if i == 0 else (),
            tax_account_ref=VAT if i in tax else None,
            tax_amount=Decimal(tax.get(i, "0")),
        )
        for i, line in enumerate(claim.lines)
        if (line.approved_amount or 0) > 0
    )
    base = {
        "claim_id": claim.id,
        "liability_account_ref": LIABILITY_ACCOUNT,
        "acceptance_reference": "ACC-1",
        "accepted_by": "finance:one",
        "lines": lines,
        "input_vat_mode": mode,
    }
    base.update(overrides)
    return AcceptClaimForReimbursement(**base)  # type: ignore[arg-type]


def _accept(db: Session, claim: ExpenseClaim, **kw: object) -> ClaimLiability:
    command = kw.pop("command", None) or _command(claim)
    return accept_claim_for_reimbursement(
        db,
        scope=_scope(),
        command=command,  # type: ignore[arg-type]
        accepted_at=kw.pop("accepted_at", NOW),  # type: ignore[arg-type]
        **{**_key(), **kw},  # type: ignore[arg-type]
    )


def _liability(db: Session, approved: tuple[str, ...] = ("100.00",), **kw):
    claim = _approved_claim(db, approved=approved, **kw)
    return claim, _accept(db, claim)


def _settle(db, liability, amount, ref="PAY-1", **kw):
    args = {
        "scope": _scope(),
        "liability_id": liability.id,
        "amount": amount if isinstance(amount, Money) else Money.of(amount, NGN),
        "settlement_ref": ref,
        "evidence_ref": "bank:1",
        "value_date": date(2026, 11, 2),
        "recorded_by": "finance:one",
        "recorded_at": _later(),
        **_key(),
    }
    args.update(kw)
    return record_claim_settlement(db, **args)


def _adjust(db, liability, amount="-10.00", ref="ADJ-1", **kw):
    args = {
        "scope": _scope(),
        "liability_id": liability.id,
        "amount": Money.of(amount, NGN),
        "adjustment_ref": ref,
        "reason": "claimant left",
        "evidence_ref": "memo:1",
        "offset_account_ref": OFFSET,
        "adjusted_by": "finance:one",
        "adjusted_at": _later(),
        **_key(),
    }
    args.update(kw)
    return adjust_claim_liability(db, **args)


def _return(db, settlement, **kw):
    args = {
        "scope": _scope(),
        "settlement_id": settlement.id,
        "return_ref": "RET-1",
        "reason": "bank returned the credit",
        "reversed_by": "finance:two",
        "reversed_at": _later(30),
        **_key(),
    }
    args.update(kw)
    return reverse_claim_settlement(db, **args)


def _receipt(db, document_kind, document_id, fingerprint, **kw):
    return record_claim_accounting_receipt(
        db,
        scope=_scope(),
        command=ClaimAccountingReceiptInput(
            document_kind=document_kind,
            document_id=document_id,
            consequence_fingerprint=fingerprint,
            accounting_reference="GL-1",
            accounting_evidence_fingerprint="e" * 64,
            **kw,
        ),
        recorded_at=_later(),
        **_key(),
    )


def _post_settlement(db, settlement):
    consequence = build_claim_settlement_consequence(
        db,
        tenant_id=TENANT,
        settlement_id=settlement.id,
        bank_account_ref=BANK,
    )
    return _receipt(
        db,
        "expense_claim_settlement",
        settlement.id,
        consequence.fingerprint,
        bank_account_ref=BANK,
    )


def _code(exc: pytest.ExceptionInfo[ExpensesError]) -> str | None:
    return exc.value.code


# --- acceptance -----------------------------------------------------------------------


def test_accept_writes_liability_lines_event_and_leaves_claim_approved(
    db: Session,
) -> None:
    claim = _approved_claim(
        db, approved=("60.00", "0.00", "40.00"), claimed=("60.00", "30.00", "50.00")
    )
    liability = _accept(db, claim)
    assert liability.amount == Decimal("100.00")
    assert liability.status == ClaimLiabilityStatus.OPEN.value
    assert liability.origin == LiabilityOrigin.ACCEPTED.value
    assert liability.claimant_party_id == CLAIMANT
    assert liability.currency_code == "NGN"
    assert liability.minor_units == 2
    assert liability.input_vat_mode == "none"
    assert claim.status == ClaimStatus.APPROVED
    lines = list(
        db.scalars(
            select(ClaimLiabilityLine)
            .where(ClaimLiabilityLine.liability_id == liability.id)
            .order_by(ClaimLiabilityLine.line_number)
        )
    )
    # The zero-approved line is skipped.
    assert [(x.net_amount, x.tax_amount) for x in lines] == [
        (Decimal("60.00"), Decimal("0")),
        (Decimal("40.00"), Decimal("0")),
    ]
    assert lines[0].dimension_refs == [["project", "p1"]]
    events = [
        (e.from_status, e.to_status)
        for e in db.scalars(
            select(ExpenseLifecycleEvent).where(
                ExpenseLifecycleEvent.claim_id == claim.id
            )
        )
    ]
    assert ("approved", "liability_open") in events


def test_accept_tax_split_under_immediate_mode(db: Session) -> None:
    claim = _approved_claim(db, approved=("107.50",))
    liability = _accept(
        db,
        claim,
        command=_command(claim, tax={0: "7.50"}, mode=ClaimInputVat.IMMEDIATE),
    )
    line = db.scalar(
        select(ClaimLiabilityLine).where(
            ClaimLiabilityLine.liability_id == liability.id
        )
    )
    assert (line.net_amount, line.tax_amount, line.tax_account_ref) == (
        Decimal("100.00"),
        Decimal("7.50"),
        VAT,
    )
    assert liability.input_vat_mode == "immediate"
    consequence = build_claim_liability_consequence(
        db, tenant_id=TENANT, liability_id=liability.id
    )
    by_account = {e.account_ref: (e.debit, e.credit) for e in consequence.entries}
    assert by_account == {
        EXPENSE: (Decimal("100.00"), Decimal(0)),
        VAT: (Decimal("7.50"), Decimal(0)),
        LIABILITY_ACCOUNT: (Decimal(0), Decimal("107.50")),
    }


def test_default_mode_is_none_and_refuses_a_tax_amount(db: Session) -> None:
    assert CLAIM_INPUT_VAT_DEFAULT is ClaimInputVat.NONE
    claim = _approved_claim(db, approved=("107.50",))
    with pytest.raises(InvalidCommand) as exc:
        _accept(db, claim, command=_command(claim, tax={0: "7.50"}))
    assert _code(exc) == RefusalCode.CLAIM_VAT_DISABLED
    assert "(claim_vat_disabled)" in str(exc.value)


def test_immediate_mode_only_reaches_the_immediate_account(db: Session) -> None:
    claim = _approved_claim(db, approved=("107.50",))
    command = _command(claim, tax={0: "7.50"}, mode=ClaimInputVat.IMMEDIATE)
    deferred = replace(
        command.lines[0], tax_account_ref="1455"
    )  # the deferral account is never valid
    with pytest.raises(InvalidCommand) as exc:
        _accept(db, claim, command=replace(command, lines=(deferred,)))
    assert _code(exc) == RefusalCode.CLAIM_VAT_ACCOUNT_NOT_IMMEDIATE


def test_vat_setting_declaration_and_fail_closed_parse() -> None:
    spec = claim_input_vat_spec()
    assert CLAIM_INPUT_VAT_SETTING == "expenses.claim_input_vat"
    assert (str(spec.domain), spec.key) == ("expenses", "claim_input_vat")
    assert spec.default == "none"
    assert spec.allowed == {"none", "immediate"}
    assert parse_claim_input_vat("immediate") is ClaimInputVat.IMMEDIATE
    with pytest.raises(InvalidCommand):
        parse_claim_input_vat("deferred")
    # The declaration is not registered at import (the Starter does not compose
    # Expenses), so a Starter-only run never names an undeclared setting domain.
    from dotmac_kernel.settings_resolver import all_specs

    assert ("expenses", "claim_input_vat") not in {
        (str(s.domain), s.key) for s in all_specs()
    }


def test_the_frozen_vat_mode_never_changes_with_the_setting(db: Session) -> None:
    claim = _approved_claim(db, approved=("107.50",))
    liability = _accept(
        db,
        claim,
        command=_command(claim, tax={0: "7.50"}, mode=ClaimInputVat.IMMEDIATE),
    )
    # A later replay under a changed setting cannot alter the frozen facts: the
    # same acceptance under mode 'none' is a different request.
    with pytest.raises(Conflict) as exc:
        _accept(db, claim, command=_command(claim))
    assert _code(exc) == RefusalCode.CLAIM_ALREADY_ACCEPTED
    assert liability.input_vat_mode == "immediate"


@pytest.mark.parametrize("which", ["missing", "unknown", "duplicate"])
def test_acceptance_lines_must_match_the_approved_lines(
    db: Session, which: str
) -> None:
    claim = _approved_claim(db, approved=("60.00", "40.00"))
    command = _command(claim)
    if which == "missing":
        command = replace(command, lines=command.lines[:1])
    elif which == "unknown":
        command = replace(
            command,
            lines=(
                *command.lines,
                replace(command.lines[0], claim_line_id=uuid.uuid4()),
            ),
        )
    else:
        command = replace(command, lines=(*command.lines, command.lines[0]))
    with pytest.raises(InvalidCommand) as exc:
        _accept(db, claim, command=command)
    assert _code(exc) == RefusalCode.ACCEPTANCE_LINES_MISMATCH


@pytest.mark.parametrize(
    ("expense", "tax", "liability_ref"),
    [
        (LIABILITY_ACCOUNT, None, LIABILITY_ACCOUNT),
        (EXPENSE, LIABILITY_ACCOUNT, LIABILITY_ACCOUNT),
        (EXPENSE, EXPENSE, LIABILITY_ACCOUNT),
    ],
)
def test_acceptance_account_conflicts(
    db: Session, expense: str, tax: str | None, liability_ref: str
) -> None:
    claim = _approved_claim(db, approved=("100.00",))
    command = _command(claim, liability_account_ref=liability_ref)
    line = replace(
        command.lines[0],
        expense_account_ref=expense,
        tax_account_ref=tax,
        tax_amount=Decimal("5.00") if tax else Decimal("0"),
    )
    with pytest.raises(InvalidCommand) as exc:
        _accept(
            db,
            claim,
            command=replace(
                command, lines=(line,), input_vat_mode=ClaimInputVat.IMMEDIATE
            ),
        )
    assert _code(exc) == RefusalCode.ACCEPTANCE_ACCOUNT_CONFLICT


def test_tax_must_be_less_than_the_approved_amount(db: Session) -> None:
    claim = _approved_claim(db, approved=("10.00",))
    with pytest.raises(InvalidCommand):
        _accept(
            db,
            claim,
            command=_command(claim, tax={0: "10.00"}, mode=ClaimInputVat.IMMEDIATE),
        )


def test_acceptance_needs_an_eligible_approved_claim(db: Session) -> None:
    draftish = _approved_claim(db, decide=False)  # submitted, not approved
    command = AcceptClaimForReimbursement(
        claim_id=draftish.id,
        liability_account_ref=LIABILITY_ACCOUNT,
        acceptance_reference="ACC-X",
        accepted_by="finance:one",
        lines=(AcceptedClaimLine(draftish.lines[0].id, EXPENSE),),
    )
    with pytest.raises(InvalidLifecycle) as exc:
        _accept(db, draftish, command=command)
    assert _code(exc) == RefusalCode.CLAIM_NOT_ELIGIBLE
    assert "claim.not_approved" in str(exc.value)
    with pytest.raises(NotFound):
        _accept(db, draftish, command=replace(command, claim_id=uuid.uuid4()))


def test_acceptance_replay_and_conflicts(db: Session) -> None:
    claim = _approved_claim(db)
    key = uuid.uuid4().hex
    first = _accept(db, claim, idempotency_key=key)
    # Same key, same request: replayed.
    assert _accept(db, claim, idempotency_key=key).id == first.id
    # New key, identical facts: the permanent row replays, no second liability.
    assert _accept(db, claim).id == first.id
    # Any differing field, actor and reference included, is a conflict.
    for override in (
        {"acceptance_reference": "ACC-2"},
        {"accepted_by": "finance:other"},
        {"liability_account_ref": "acct:other"},
    ):
        with pytest.raises(Conflict) as exc:
            _accept(db, claim, command=_command(claim, **override))
        assert _code(exc) == RefusalCode.CLAIM_ALREADY_ACCEPTED
    # The same key with a different request is refused by the idempotency ledger.
    with pytest.raises(Conflict) as exc:
        _accept(
            db, claim, idempotency_key=key, command=_command(claim, accepted_by="x")
        )
    assert _code(exc) == RefusalCode.IDEMPOTENCY_KEY_REUSED
    assert len(list(db.scalars(select(ClaimLiability)))) == 1


def test_withdraw_claim_approval_is_blocked_while_a_live_liability_exists(
    db: Session,
) -> None:
    claim, liability = _liability(db)
    with pytest.raises(InvalidLifecycle) as exc:
        withdraw_claim_approval(
            db,
            scope=_scope(),
            claim_id=claim.id,
            actor_reference="finance:one",
            decision_reference="withdraw-1",
            withdrawn_at=_later(),
            reason="mistake",
        )
    assert _code(exc) == RefusalCode.CLAIM_HAS_LIABILITY
    assert claim.status == ClaimStatus.APPROVED
    reverse_claim_acceptance(
        db,
        scope=_scope(),
        liability_id=liability.id,
        reason="accepted in error",
        evidence_ref="memo:9",
        reversed_by="finance:one",
        reversed_at=_later(),
        **_key(),
    )
    withdrawn = withdraw_claim_approval(
        db,
        scope=_scope(),
        claim_id=claim.id,
        actor_reference="finance:one",
        decision_reference="withdraw-1",
        withdrawn_at=_later(),
        reason="mistake",
    )
    assert withdrawn.status == ClaimStatus.APPROVAL_WITHDRAWN


def test_a1_refusals_carry_no_code(db: Session) -> None:
    with pytest.raises(NotFound) as exc:
        withdraw_claim_approval(
            db,
            scope=_scope(),
            claim_id=uuid.uuid4(),
            actor_reference="x",
            decision_reference="y",
            withdrawn_at=NOW,
            reason="z",
        )
    assert exc.value.code is None


# --- reversal of acceptance -----------------------------------------------------------


def test_reverse_acceptance_is_terminal_and_replays(db: Session) -> None:
    _, liability = _liability(db)
    args = {
        "scope": _scope(),
        "liability_id": liability.id,
        "reason": "accepted in error",
        "evidence_ref": "memo:9",
        "reversed_by": "finance:one",
        "reversed_at": _later(),
    }
    reversal = reverse_claim_acceptance(db, **args, **_key())
    assert liability.status == ClaimLiabilityStatus.REVERSED.value
    # New key, identical facts: replayed; differing: conflict.
    assert reverse_claim_acceptance(db, **args, **_key()).id == reversal.id
    with pytest.raises(Conflict):
        reverse_claim_acceptance(db, **{**args, "reason": "other"}, **_key())
    # Terminal: nothing more can happen to a reversed liability.
    with pytest.raises(InvalidLifecycle) as exc:
        _settle(db, liability, "10.00")
    assert _code(exc) == RefusalCode.LIABILITY_REVERSED
    with pytest.raises(InvalidLifecycle) as exc:
        _adjust(db, liability)
    assert _code(exc) == RefusalCode.LIABILITY_REVERSED
    # The reversal mirrors the acceptance exactly.
    accepted = build_claim_liability_consequence(
        db, tenant_id=TENANT, liability_id=liability.id
    )
    mirror = build_claim_liability_reversal_consequence(
        db, tenant_id=TENANT, reversal_id=reversal.id
    )
    assert {(e.account_ref, e.debit, e.credit) for e in mirror.entries} == {
        (e.account_ref, e.credit, e.debit) for e in accepted.entries
    }
    assert mirror.fingerprint != accepted.fingerprint
    assert mirror.source.document_kind == "expense_claim_liability_reversal"
    # A reversed liability leaves every balance and the control total.
    assert claims_payable_total(
        db, tenant_id=TENANT, liability_account_ref=LIABILITY_ACCOUNT
    ) == Decimal(0)


def test_reverse_acceptance_refused_once_touched(db: Session) -> None:
    for touch in ("settled", "returned", "adjusted", "adjusted_back"):
        _, liability = _liability(db)
        if touch == "settled":
            _settle(db, liability, "10.00")
        elif touch == "returned":
            settlement = _settle(db, liability, "10.00")
            _post_settlement(db, settlement)
            _return(db, settlement)
        elif touch == "adjusted":
            _adjust(db, liability, "-10.00")
        else:
            _adjust(db, liability, "-10.00", ref="A1")
            _adjust(db, liability, "10.00", ref="A2")
        with pytest.raises(InvalidLifecycle) as exc:
            reverse_claim_acceptance(
                db,
                scope=_scope(),
                liability_id=liability.id,
                reason="oops",
                evidence_ref="memo:1",
                reversed_by="finance:one",
                reversed_at=_later(60),
                **_key(),
            )
        assert _code(exc) == RefusalCode.LIABILITY_NOT_OPEN, touch


# --- settlement -----------------------------------------------------------------------


def test_partial_then_full_settlement_derives_status(db: Session) -> None:
    _, liability = _liability(db)
    _settle(db, liability, "40.00", ref="P1")
    assert liability.status == ClaimLiabilityStatus.PARTIALLY_SETTLED.value
    assert liability.amount_settled == Decimal("40.00")
    _settle(db, liability, "60.00", ref="P2")
    assert liability.status == ClaimLiabilityStatus.SETTLED.value
    with pytest.raises(InvalidCommand) as exc:
        _settle(db, liability, "0.01", ref="P3")
    assert _code(exc) == RefusalCode.SETTLEMENT_EXCEEDS_OUTSTANDING


def test_over_settlement_wrong_currency_and_non_positive_are_refused(
    db: Session,
) -> None:
    _, liability = _liability(db)
    with pytest.raises(InvalidCommand) as exc:
        _settle(db, liability, "100.01")
    assert _code(exc) == RefusalCode.SETTLEMENT_EXCEEDS_OUTSTANDING
    with pytest.raises(InvalidCommand, match="currency"):
        _settle(db, liability, Money.of("10", Currency("USD", 2)))
    with pytest.raises(InvalidCommand, match="positive"):
        _settle(db, liability, "0.00")
    assert liability.amount_settled == 0


def test_settlement_replay_conflict_and_repay_after_return(db: Session) -> None:
    _, liability = _liability(db)
    first = _settle(db, liability, "30.00", ref="P1")
    assert _settle(db, liability, "30.00", ref="P1").id == first.id
    assert liability.amount_settled == Decimal("30.00")
    with pytest.raises(Conflict):
        _settle(db, liability, "31.00", ref="P1")
    with pytest.raises(Conflict):
        _settle(db, liability, "30.00", ref="P1", evidence_ref="bank:2")
    _post_settlement(db, first)
    reversal = _return(db, first)
    assert liability.amount_settled == 0
    assert liability.status == ClaimLiabilityStatus.OPEN.value
    # A replay of the returned settlement is refused: re-pay under a new ref.
    with pytest.raises(Conflict, match="new reference"):
        _settle(db, liability, "30.00", ref="P1")
    again = _settle(db, liability, "30.00", ref="P1-REPAY")
    assert again.id != first.id
    assert liability.amount_settled == Decimal("30.00")
    assert reversal.amount == Decimal("30.00")


def test_return_needs_the_settlement_in_the_ledger(db: Session) -> None:
    _, liability = _liability(db)
    settlement = _settle(db, liability, "100.00")
    with pytest.raises(InvalidLifecycle) as exc:
        _return(db, settlement)
    assert _code(exc) == RefusalCode.SETTLEMENT_NOT_IN_LEDGER
    assert liability.status == ClaimLiabilityStatus.SETTLED.value
    _post_settlement(db, settlement)
    reversal = _return(db, settlement)
    assert liability.status == ClaimLiabilityStatus.OPEN.value
    assert reversal.liability_id == liability.id


def test_a_partial_settlement_can_be_returned_and_reopens_partially(
    db: Session,
) -> None:
    _, liability = _liability(db)
    first = _settle(db, liability, "30.00", ref="P1")
    _settle(db, liability, "20.00", ref="P2")
    _post_settlement(db, first)
    _return(db, first)
    assert liability.amount_settled == Decimal("20.00")
    assert liability.status == ClaimLiabilityStatus.PARTIALLY_SETTLED.value


def test_return_replay_conflict_and_actor_policy(db: Session) -> None:
    _, liability = _liability(db)
    settlement = _settle(db, liability, "100.00", recorded_by="finance:one")
    _post_settlement(db, settlement)
    # No compulsory second person: the recorder may return by default.
    # With the caller's policy on, the same actor is refused.
    with pytest.raises(InvalidLifecycle) as exc:
        _return(db, settlement, reversed_by="finance:one", require_distinct_actor=True)
    assert _code(exc) == RefusalCode.DISTINCT_ACTOR_REQUIRED
    with pytest.raises(InvalidCommand, match="before it was recorded"):
        _return(db, settlement, reversed_at=_later(-60))
    reversal = _return(db, settlement, reversed_by="finance:one")
    assert _return(db, settlement, reversed_by="finance:one").id == reversal.id
    with pytest.raises(Conflict):
        _return(db, settlement, reversed_by="finance:one", reason="different")
    assert liability.amount_settled == 0


# --- adjustment -----------------------------------------------------------------------


def test_write_down_increase_and_full_write_off(db: Session) -> None:
    _, liability = _liability(db)
    row = _adjust(db, liability, "-30.00")
    assert row.amount == Decimal("-30.00")
    assert liability.amount == Decimal("100.00")
    assert liability.amount_adjusted == Decimal("-30.00")
    assert effective_claim_liability_amount(liability) == Decimal("70.00")
    assert liability.status == ClaimLiabilityStatus.OPEN.value
    # Settlement compares against the effective amount.
    with pytest.raises(InvalidCommand):
        _settle(db, liability, "70.01")
    _settle(db, liability, "70.00")
    assert liability.status == ClaimLiabilityStatus.SETTLED.value
    # A forward increase reopens it.
    _adjust(db, liability, "25.00", ref="ADJ-2")
    assert liability.status == ClaimLiabilityStatus.PARTIALLY_SETTLED.value
    assert claim_balance(db) == Decimal("25.00")


def claim_balance(db: Session) -> Decimal:
    return claimant_claims_balance(
        db, tenant_id=TENANT, claimant_party_id=CLAIMANT, currency_code="NGN"
    )


def test_full_write_down_is_settled_with_nothing_settled(db: Session) -> None:
    _, liability = _liability(db)
    _adjust(db, liability, "-100.00")
    assert liability.status == ClaimLiabilityStatus.SETTLED.value
    assert liability.amount_settled == 0
    assert effective_claim_liability_amount(liability) == 0
    assert claim_balance(db) == 0


def test_adjustment_refusals_and_replay(db: Session) -> None:
    _, liability = _liability(db)
    _settle(db, liability, "60.00")
    with pytest.raises(InvalidCommand) as exc:
        _adjust(db, liability, "-40.01")
    assert _code(exc) == RefusalCode.ADJUSTMENT_EXCEEDS_OUTSTANDING
    with pytest.raises(InvalidCommand) as exc:
        _adjust(db, liability, "-10.00", offset_account_ref=LIABILITY_ACCOUNT)
    assert _code(exc) == RefusalCode.OFFSET_IS_LIABILITY_ACCOUNT
    with pytest.raises(InvalidCommand, match="zero"):
        _adjust(db, liability, "0.00")
    with pytest.raises(InvalidCommand, match="before it existed"):
        _adjust(db, liability, "-1.00", adjusted_at=NOW - timedelta(days=1))
    with pytest.raises(InvalidCommand, match="timezone"):
        _adjust(db, liability, "-1.00", adjusted_at=datetime(2026, 11, 5))
    first = _adjust(db, liability, "-10.00", ref="A1")
    assert _adjust(db, liability, "-10.00", ref="A1").id == first.id
    assert liability.amount_adjusted == Decimal("-10.00")
    with pytest.raises(Conflict):
        _adjust(db, liability, "-11.00", ref="A1")
    with pytest.raises(Conflict):
        _adjust(db, liability, "-10.00", ref="A1", reason="other reason")


# --- consequences ---------------------------------------------------------------------


def test_every_consequence_shape_balance_and_stable_fingerprint(db: Session) -> None:
    claim = _approved_claim(db, approved=("60.00", "47.50"))
    liability = _accept(
        db,
        claim,
        command=_command(claim, tax={1: "7.50"}, mode=ClaimInputVat.IMMEDIATE),
    )
    accepted = build_claim_liability_consequence(
        db, tenant_id=TENANT, liability_id=liability.id
    )
    again = build_claim_liability_consequence(
        db, tenant_id=TENANT, liability_id=liability.id
    )
    assert accepted.fingerprint == again.fingerprint
    assert accepted.posting_date == date(2026, 11, 1)
    assert accepted.currency_code == "NGN"
    assert sum(e.debit for e in accepted.entries) == sum(
        e.credit for e in accepted.entries
    )
    assert sum(e.credit for e in accepted.entries) == Decimal("107.50")
    assert [e.debit for e in accepted.entries] == [
        Decimal("60.00"),
        Decimal("40.00"),
        Decimal("7.50"),
        Decimal(0),
    ] or {e.account_ref for e in accepted.entries} == {
        EXPENSE,
        EXPENSE_2,
        VAT,
        LIABILITY_ACCOUNT,
    }
    dimensioned = next(e for e in accepted.entries if e.account_ref == EXPENSE)
    assert dimensioned.dimension_refs == (("project", "p1"),)
    assert accepted.idempotency_key.startswith(
        f"expenses:expense_claim_liability:{TENANT}"
    )

    settlement = _settle(db, liability, "50.00")
    settled = build_claim_settlement_consequence(
        db, tenant_id=TENANT, settlement_id=settlement.id, bank_account_ref=BANK
    )
    assert [(e.account_ref, e.debit, e.credit) for e in settled.entries] == [
        (LIABILITY_ACCOUNT, Decimal("50.00"), Decimal(0)),
        (BANK, Decimal(0), Decimal("50.00")),
    ]
    assert settled.posting_date == date(2026, 11, 2)
    other_bank = build_claim_settlement_consequence(
        db, tenant_id=TENANT, settlement_id=settlement.id, bank_account_ref="acct:b2"
    )
    assert other_bank.fingerprint != settled.fingerprint
    with pytest.raises(InvalidCommand, match="bank account must differ"):
        build_claim_settlement_consequence(
            db,
            tenant_id=TENANT,
            settlement_id=settlement.id,
            bank_account_ref=LIABILITY_ACCOUNT,
        )

    _post_settlement(db, settlement)
    reversal = _return(db, settlement)
    returned = build_claim_settlement_reversal_consequence(
        db,
        tenant_id=TENANT,
        reversal_id=reversal.id,
        bank_account_ref=BANK,
        posting_date=date(2026, 11, 3),
    )
    assert [(e.account_ref, e.debit, e.credit) for e in returned.entries] == [
        (BANK, Decimal("50.00"), Decimal(0)),
        (LIABILITY_ACCOUNT, Decimal(0), Decimal("50.00")),
    ]

    down = _adjust(db, liability, "-20.00", ref="D")
    up = _adjust(db, liability, "5.00", ref="U")
    down_c = build_claim_liability_adjustment_consequence(
        db, tenant_id=TENANT, adjustment_id=down.id, posting_date=date(2026, 11, 4)
    )
    up_c = build_claim_liability_adjustment_consequence(
        db, tenant_id=TENANT, adjustment_id=up.id, posting_date=date(2026, 11, 4)
    )
    assert [(e.account_ref, e.debit, e.credit) for e in down_c.entries] == [
        (LIABILITY_ACCOUNT, Decimal("20.00"), Decimal(0)),
        (OFFSET, Decimal(0), Decimal("20.00")),
    ]
    assert [(e.account_ref, e.debit, e.credit) for e in up_c.entries] == [
        (OFFSET, Decimal("5.00"), Decimal(0)),
        (LIABILITY_ACCOUNT, Decimal(0), Decimal("5.00")),
    ]
    for missing in (
        lambda: build_claim_liability_consequence(
            db, tenant_id=TENANT, liability_id=uuid.uuid4()
        ),
        lambda: build_claim_liability_consequence(
            db, tenant_id=OTHER_TENANT, liability_id=liability.id
        ),
    ):
        with pytest.raises(NotFound):
            missing()


# --- accounting receipt ---------------------------------------------------------------


def test_accounting_receipt_is_bound_to_the_rebuilt_fingerprint(db: Session) -> None:
    _, liability = _liability(db)
    consequence = build_claim_liability_consequence(
        db, tenant_id=TENANT, liability_id=liability.id
    )
    with pytest.raises(Conflict, match="does not match"):
        _receipt(db, "expense_claim_liability", liability.id, "f" * 64)
    with pytest.raises(InvalidCommand, match="SHA-256"):
        _receipt(db, "expense_claim_liability", liability.id, "nope")
    with pytest.raises(InvalidCommand, match="unknown"):
        _receipt(db, "supplier_invoice", liability.id, consequence.fingerprint)
    receipt = _receipt(
        db, "expense_claim_liability", liability.id, consequence.fingerprint
    )
    again = _receipt(
        db, "expense_claim_liability", liability.id, consequence.fingerprint
    )
    assert again.id == receipt.id
    with pytest.raises(Conflict, match="different accounting evidence"):
        record_claim_accounting_receipt(
            db,
            scope=_scope(),
            command=ClaimAccountingReceiptInput(
                document_kind="expense_claim_liability",
                document_id=liability.id,
                consequence_fingerprint=consequence.fingerprint,
                accounting_reference="GL-OTHER",
                accounting_evidence_fingerprint="e" * 64,
            ),
            recorded_at=_later(),
            **_key(),
        )
    assert len(list(db.scalars(select(ClaimAccountingReceipt)))) == 1


def test_settlement_and_return_receipts_need_their_extra_inputs(db: Session) -> None:
    _, liability = _liability(db)
    settlement = _settle(db, liability, "100.00")
    with pytest.raises(InvalidCommand, match="bank account"):
        _receipt(db, "expense_claim_settlement", settlement.id, "a" * 64)
    _post_settlement(db, settlement)
    reversal = _return(db, settlement)
    with pytest.raises(InvalidCommand, match="posting date"):
        _receipt(
            db,
            "expense_claim_settlement_reversal",
            reversal.id,
            "a" * 64,
            bank_account_ref=BANK,
        )
    returned = build_claim_settlement_reversal_consequence(
        db,
        tenant_id=TENANT,
        reversal_id=reversal.id,
        bank_account_ref=BANK,
        posting_date=date(2026, 11, 3),
    )
    assert _receipt(
        db,
        "expense_claim_settlement_reversal",
        reversal.id,
        returned.fingerprint,
        bank_account_ref=BANK,
        posting_date=date(2026, 11, 3),
    )


# --- reads ----------------------------------------------------------------------------


def test_per_employee_balance_control_total_and_open_selector(db: Session) -> None:
    _, first = _liability(db, approved=("100.00",))
    _, second = _liability(db, approved=("50.00",))
    _, other = _liability(db, approved=("70.00",), claimant=OTHER_CLAIMANT)
    _, closed = _liability(db, approved=("10.00",))
    _settle(db, first, "25.00")
    _settle(db, closed, "10.00", ref="PAY-C")
    assert claim_balance(db) == Decimal("75.00") + Decimal("50.00")
    assert claimant_claims_balance(
        db, tenant_id=TENANT, claimant_party_id=OTHER_CLAIMANT, currency_code="ngn"
    ) == Decimal("70.00")
    assert claims_payable_total(
        db, tenant_id=TENANT, liability_account_ref=LIABILITY_ACCOUNT
    ) == Decimal("195.00")
    assert claims_payable_total(
        db, tenant_id=TENANT, liability_account_ref="acct:none"
    ) == Decimal(0)
    opened = {row.id for row in open_claim_liabilities(db, tenant_id=TENANT)}
    assert opened == {first.id, second.id, other.id}  # the settled one is gone
    assert {
        row.id
        for row in open_claim_liabilities(
            db, tenant_id=TENANT, claimant_party_id=OTHER_CLAIMANT
        )
    } == {other.id}
    assert (
        claimant_claims_balance(
            db, tenant_id=OTHER_TENANT, claimant_party_id=CLAIMANT, currency_code="NGN"
        )
        == 0
    )


def _assert_invariants(db: Session) -> None:
    for liability in db.scalars(select(ClaimLiability)):
        effective = effective_claim_liability_amount(liability)
        settled, adjusted = claim_liability_row_totals(
            db, tenant_id=TENANT, liability_id=liability.id
        )
        # Rows sum to the projection columns, and the CHECK rules hold.
        assert settled == liability.amount_settled
        assert adjusted == liability.amount_adjusted
        assert liability.amount > 0
        assert 0 <= liability.amount_settled <= effective
        if liability.status == ClaimLiabilityStatus.REVERSED.value:
            assert liability.amount_settled == 0 and liability.amount_adjusted == 0
        elif liability.amount_settled == effective:
            assert liability.status == ClaimLiabilityStatus.SETTLED.value
        elif liability.amount_settled == 0:
            assert liability.status == ClaimLiabilityStatus.OPEN.value
        else:
            assert liability.status == ClaimLiabilityStatus.PARTIALLY_SETTLED.value


@pytest.mark.parametrize("seed", [1, 2, 3, 5, 8])
def test_randomised_event_sequences_keep_every_invariant(
    db: Session, seed: int
) -> None:
    rng = _Lcg(seed)
    liabilities = [_liability(db, approved=("100.00",))[1] for _ in range(3)]
    settlements: list[object] = []
    done = {"settle": 0, "return": 0, "down": 0, "up": 0, "reverse": 0}
    counter = 0
    for _ in range(60):
        counter += 1
        liability = rng.choice(liabilities)
        op = rng.choice(["settle", "return", "down", "up", "reverse"])
        try:
            if op == "settle":
                amount = Decimal(rng.randint(1, 4000)) / 100
                settlements.append(
                    _settle(db, liability, str(amount), ref=f"P{counter}")
                )
            elif op == "return" and settlements:
                settlement = rng.choice(settlements)
                _post_settlement(db, settlement)
                _return(db, settlement, return_ref=f"R{counter}")
            elif op == "down":
                _adjust(
                    db,
                    liability,
                    str(-Decimal(rng.randint(1, 3000)) / 100),
                    ref=f"A{counter}",
                )
            elif op == "up":
                _adjust(
                    db,
                    liability,
                    str(Decimal(rng.randint(1, 3000)) / 100),
                    ref=f"A{counter}",
                )
            elif op == "reverse":
                reverse_claim_acceptance(
                    db,
                    scope=_scope(),
                    liability_id=liability.id,
                    reason="r",
                    evidence_ref="e",
                    reversed_by="f",
                    reversed_at=_later(90),
                    **_key(),
                )
            done[op] += 1
        except ExpensesError:
            pass  # a refusal must leave every invariant intact
        db.flush()
        _assert_invariants(db)
    # The sequence is not vacuous: real transitions happened.
    assert done["settle"] > 0 and sum(done.values()) >= 8, done


def test_the_database_checks_refuse_an_inconsistent_status(db: Session) -> None:
    _, liability = _liability(db)
    liability.status = ClaimLiabilityStatus.SETTLED.value  # amounts say open
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_the_database_checks_refuse_over_settlement_and_negative_effective(
    db: Session,
) -> None:
    claim = _approved_claim(db)
    for amounts in (
        {"amount_settled": Decimal("100.01"), "status": "settled"},
        {"amount_adjusted": Decimal("-100.01")},
        {"amount": Decimal("0")},
    ):
        row = ClaimLiability(
            tenant_id=TENANT,
            claim_id=claim.id,
            claimant_party_id=CLAIMANT,
            currency_code="NGN",
            minor_units=2,
            liability_account_ref=LIABILITY_ACCOUNT,
            origin="accepted",
            input_vat_mode="none",
            acceptance_reference="X",
            accepted_by="a",
            accepted_at=NOW,
            **{"amount": Decimal("100.00"), "status": "open", **amounts},
        )
        db.add(row)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


# --- historical admission -------------------------------------------------------------


class _Verifier:
    def __init__(self, evidence_for) -> None:
        self.evidence_for = evidence_for
        self.calls = 0

    def verify(self, *, scope, command, destination):
        self.calls += 1
        return self.evidence_for(destination, command)


def _hist_settlement(
    amount: str = "40.00", ref: str = "J-1"
) -> HistoricalClaimSettlement:
    return HistoricalClaimSettlement(ref, Decimal(amount), "cv1:" + "1" * 64)


def _evidence(
    claim: ExpenseClaim, **overrides: object
) -> HistoricalClaimLiabilityEvidence:
    base = {
        "tenant_id": TENANT,
        "source_owner": "dotmac_erp",
        "source_claim_id": "legacy-claim-1",
        "source_snapshot_sha256": "a" * 64,
        "claim_id": claim.id,
        "claimant_party_id": claim.claimant_party_id,
        "currency_code": "NGN",
        "net_payable_amount": Decimal("100.00"),
        "settled_before_admission": Decimal("40.00"),
        "residual": Decimal("60.00"),
        "settlements": (_hist_settlement(),),
        "liability_account_ref": LIABILITY_ACCOUNT,
        "carried_control_amount": Decimal("60.00"),
        "carried_account_ref": "acct:legacy-2030",
        "carried_evidence_ref": "gl-extract:1",
        "source_journal_ref": "JE-100",
        "original_posted_at": datetime(2026, 9, 1, tzinfo=UTC),
        "accounting_evidence_fingerprint": "cv1:" + "2" * 64,
        "facts_fingerprint": "cv1:" + "3" * 64,
    }
    base.update(overrides)
    return HistoricalClaimLiabilityEvidence(**base)  # type: ignore[arg-type]


def _hist_command(claim, evidence, **overrides) -> HistoricalClaimLiabilityCommand:
    base = {
        "source_owner": evidence.source_owner,
        "source_claim_id": evidence.source_claim_id,
        "claim_id": claim.id,
        "source_snapshot_sha256": evidence.source_snapshot_sha256,
        "evidence_fingerprint": evidence.fingerprint(),
        "evidence_reference": "finance:review-7",
        "admitted_by": "finance:one",
    }
    base.update(overrides)
    return HistoricalClaimLiabilityCommand(**base)


def _admit(db, claim, evidence=None, command=None, verifier=None, **kw):
    evidence = evidence or _evidence(claim)
    command = command or _hist_command(claim, evidence)
    verifier = verifier or _Verifier(lambda _db, _cmd: evidence)
    return admit_historical_claim_liability(
        db,
        scope=_scope(),
        command=command,
        verifier=verifier,
        admitted_at=kw.pop("admitted_at", _later(60)),
        **{**_key(), **kw},
    )


def _imported_claim(db: Session) -> ExpenseClaim:
    # An imported claim keeps whatever a1 status it was imported with.
    return _approved_claim(db, approved=("100.00",))


def test_admission_creates_an_open_zero_gl_liability(db: Session) -> None:
    claim = _imported_claim(db)
    receipt = _admit(db, claim)
    liability = db.get(ClaimLiability, receipt.liability_id)
    assert liability.origin == LiabilityOrigin.HISTORICAL.value
    assert liability.amount == Decimal("60.00")
    assert liability.status == ClaimLiabilityStatus.OPEN.value
    assert liability.input_vat_mode is None
    assert receipt.residual == Decimal("60.00")
    assert receipt.carried_account_ref == "acct:legacy-2030"
    assert receipt.evidence["settlements"][0]["evidence_ref"] == "J-1"
    assert not list(db.scalars(select(ClaimLiabilityLine)))
    assert claim.status == ClaimStatus.APPROVED  # a1 status untouched
    # No GL: the acceptance consequence is refused for a historical liability.
    with pytest.raises(InvalidLifecycle) as exc:
        build_claim_liability_consequence(
            db, tenant_id=TENANT, liability_id=liability.id
        )
    assert _code(exc) == RefusalCode.HISTORICAL_LIABILITY_HAS_NO_CONSEQUENCE
    # Its later lifecycle builds normally.
    settlement = _settle(db, liability, "60.00")
    assert liability.status == ClaimLiabilityStatus.SETTLED.value
    assert build_claim_settlement_consequence(
        db, tenant_id=TENANT, settlement_id=settlement.id, bank_account_ref=BANK
    )
    adj = _adjust(db, liability, "5.00", adjusted_at=_later(120))
    assert build_claim_liability_adjustment_consequence(
        db, tenant_id=TENANT, adjustment_id=adj.id, posting_date=date(2026, 11, 9)
    )
    # And its acceptance cannot be reversed.
    with pytest.raises(InvalidLifecycle):
        reverse_claim_acceptance(
            db,
            scope=_scope(),
            liability_id=liability.id,
            reason="x",
            evidence_ref="y",
            reversed_by="z",
            reversed_at=_later(120),
            **_key(),
        )
    with pytest.raises(InvalidLifecycle) as exc:
        withdraw_claim_approval(
            db,
            scope=_scope(),
            claim_id=claim.id,
            actor_reference="f",
            decision_reference="d",
            withdrawn_at=_later(120),
            reason="r",
        )
    assert _code(exc) == RefusalCode.CLAIM_HAS_LIABILITY


def test_admission_replay_identity_includes_reference_and_actor(db: Session) -> None:
    claim = _imported_claim(db)
    evidence = _evidence(claim)
    first = _admit(db, claim, evidence=evidence)
    assert _admit(db, claim, evidence=evidence).id == first.id
    for override in (
        {"evidence_reference": "finance:review-8"},
        {"admitted_by": "finance:other"},
    ):
        with pytest.raises(Conflict):
            _admit(
                db,
                claim,
                evidence=evidence,
                command=_hist_command(claim, evidence, **override),
            )
    assert len(list(db.scalars(select(HistoricalClaimReceipt)))) == 1
    assert len(list(db.scalars(select(ClaimLiability)))) == 1


def test_admission_refuses_a_verifier_that_writes(db: Session) -> None:
    claim = _imported_claim(db)
    evidence = _evidence(claim)

    def writes(destination: Session, _command):
        destination.add(ExpenseCategory(tenant_id=TENANT, code="SNEAK", name="x"))
        return evidence

    with pytest.raises(InvalidCommand, match="must not write"):
        _admit(db, claim, evidence=evidence, verifier=_Verifier(writes))
    assert (
        db.scalar(select(ExpenseCategory).where(ExpenseCategory.code == "SNEAK"))
        is None
    )
    assert not list(db.scalars(select(HistoricalClaimReceipt)))


def test_admission_refuses_a_scoped_session_and_untrusted_inputs(db: Session) -> None:
    claim = _imported_claim(db)
    evidence = _evidence(claim)
    scoped = scoped_session(sessionmaker(bind=db.get_bind()))
    with pytest.raises(InvalidCommand, match="plain Session"):
        _admit(scoped, claim, evidence=evidence)
    scoped.remove()
    with pytest.raises(InvalidCommand, match="verifier"):
        admit_historical_claim_liability(
            db,
            scope=_scope(),
            command=_hist_command(claim, evidence),
            verifier=object(),  # type: ignore[arg-type]
            admitted_at=_later(60),
            **_key(),
        )
    with pytest.raises(InvalidCommand, match="typed evidence"):
        _admit(db, claim, verifier=_Verifier(lambda _d, _c: object()))
    with pytest.raises(InvalidCommand, match="timezone|instant"):
        _admit(db, claim, evidence=evidence, admitted_at=datetime(2026, 11, 9))


@pytest.mark.parametrize(
    "case",
    ["identity", "fingerprint", "tenant", "snapshot"],
)
def test_admission_binds_identity_and_fingerprint(db: Session, case: str) -> None:
    claim = _imported_claim(db)
    evidence = _evidence(claim)
    command = _hist_command(claim, evidence)
    if case == "identity":
        evidence = _evidence(claim, source_claim_id="other-source")
    elif case == "fingerprint":
        command = _hist_command(claim, evidence, evidence_fingerprint="cv1:" + "9" * 64)
    elif case == "tenant":
        evidence = _evidence(claim, tenant_id=OTHER_TENANT)
    else:
        evidence = _evidence(claim, source_snapshot_sha256="b" * 64)
    with pytest.raises(Conflict):
        _admit(db, claim, evidence=evidence, command=command)


def test_admission_refuses_a_claim_that_is_not_open(db: Session) -> None:
    claim = _imported_claim(db)
    evidence = _evidence(
        claim,
        settled_before_admission=Decimal("100.00"),
        residual=Decimal("0.00"),
        carried_control_amount=Decimal("0.00"),
        settlements=(_hist_settlement("100.00"),),
    )
    with pytest.raises(InvalidLifecycle) as exc:
        _admit(db, claim, evidence=evidence)
    assert _code(exc) == RefusalCode.HISTORICAL_CLAIM_NOT_OPEN


def test_admission_refuses_when_the_legacy_ledger_does_not_hold_the_residual(
    db: Session,
) -> None:
    claim = _imported_claim(db)
    evidence = _evidence(claim, carried_control_amount=Decimal("59.99"))
    with pytest.raises(InvalidCommand) as exc:
        _admit(db, claim, evidence=evidence)
    assert _code(exc) == RefusalCode.LEGACY_GL_DOES_NOT_HOLD_CLAIM


def test_admission_arithmetic_and_shape_checks(db: Session) -> None:
    claim = _imported_claim(db)
    cases = {
        "settlements do not sum": {"settlements": (_hist_settlement("39.99"),)},
        "two-decimal": {"net_payable_amount": Decimal("100.001")},
        "residual": {
            "residual": Decimal("61.00"),
            "carried_control_amount": Decimal("61.00"),
        },
        "positive": {
            "settlements": (
                _hist_settlement("50.00"),
                _hist_settlement("-10.00", "J-2"),
            )
        },
        "cv1": {"facts_fingerprint": "nope"},
        "follow admission": {"original_posted_at": datetime(2027, 1, 1, tzinfo=UTC)},
    }
    for needle, override in cases.items():
        evidence = _evidence(claim, **override)
        with pytest.raises((InvalidCommand, InvalidLifecycle, Conflict)) as exc:
            _admit(db, claim, evidence=evidence)
        assert needle.split()[0] in str(exc.value).lower() or isinstance(
            exc.value, InvalidLifecycle
        ), needle
    duplicate = _evidence(
        claim,
        settlements=(_hist_settlement("20.00"), _hist_settlement("20.00")),
    )
    with pytest.raises(Conflict, match="unique"):
        _admit(db, claim, evidence=duplicate)


def test_admission_needs_a_claimant_party(db: Session) -> None:
    claim = _imported_claim(db)
    evidence = _evidence(claim, claimant_party_id=None)
    with pytest.raises(InvalidCommand) as exc:
        _admit(db, claim, evidence=evidence)
    assert _code(exc) == RefusalCode.CLAIM_WITHOUT_EMPLOYEE


def test_admission_destination_checks(db: Session) -> None:
    claim = _imported_claim(db)
    with pytest.raises(Conflict, match="claimant or currency"):
        _admit(db, claim, evidence=_evidence(claim, claimant_party_id=OTHER_CLAIMANT))
    with pytest.raises(Conflict, match="claimant or currency"):
        _admit(db, claim, evidence=_evidence(claim, currency_code="USD"))
    with pytest.raises(NotFound):
        _admit(
            db,
            claim,
            command=_hist_command(
                _evidence(claim) and claim, _evidence(claim), claim_id=uuid.uuid4()
            ),
        )
    # A claim that already has a liability is refused.
    accepted = _approved_claim(db, approved=("100.00",))
    _accept(db, accepted)
    with pytest.raises(Conflict) as exc:
        _admit(
            db,
            accepted,
            evidence=_evidence(accepted, source_claim_id="legacy-claim-2"),
        )
    assert _code(exc) == RefusalCode.CLAIM_ALREADY_ACCEPTED


def test_one_receipt_per_source_and_per_claim(db: Session) -> None:
    first_claim = _imported_claim(db)
    _admit(db, first_claim)
    second_claim = _imported_claim(db)
    # The same legacy source cannot be admitted to a second destination claim.
    with pytest.raises(Conflict, match="source claim already admitted"):
        _admit(db, second_claim, evidence=_evidence(second_claim))


def test_a2_tables_are_declared_and_tenant_scoped() -> None:
    from dotmac_expenses import models

    for name in (
        "claim_liabilities",
        "claim_liability_lines",
        "claim_liability_settlements",
        "claim_settlement_reversals",
        "claim_liability_adjustments",
        "claim_liability_reversals",
        "historical_claim_receipts",
        "claim_accounting_receipts",
    ):
        table = models.metadata_table(name)
        assert name in TENANT_TABLES
        assert table.c.tenant_id.nullable is False


def test_adjustment_rows_are_not_a_second_status_source(db: Session) -> None:
    _, liability = _liability(db)
    row = _adjust(db, liability, "-10.00")
    assert isinstance(row, ClaimLiabilityAdjustment)
    assert not hasattr(row, "status")
