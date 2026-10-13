"""Closed opening-cash commands bind complete evidence without minting authority."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta, timezone
from decimal import (
    Clamped,
    Decimal,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    Subnormal,
    Underflow,
    getcontext,
    localcontext,
)
from uuid import UUID

import pytest
from dotmac_billing.contracts import AccountingAllocationEffectV1, AccountingEffectV1
from dotmac_billing.opening_authority import opening_cash_command_digest
from dotmac_billing.opening_cash_contracts import (
    AccountingFactV4,
    AccountingSettlementAllocationV4,
    CashCreditSourceOrderV1,
    OpeningCashEffectV1,
)
from dotmac_billing.opening_commands import (
    CorrectOpeningCashRefundV1,
    ImportOpeningAccountV1,
    OpeningCashMovementEvidenceV1,
    RecordOpeningCashRefundV1,
    ReverseCashCreditDrawV1,
    ReverseOpeningAccountImportV1,
)
from dotmac_billing.opening_contracts import OpeningEvidenceError
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Currency, Money

from tests.unit import test_billing_opening_manifest as preflight

NOW = preflight.NOW


def import_command():
    request = preflight._candidate()
    return ImportOpeningAccountV1(
        request.manifest.scope,
        request.manifest,
        request.partition,
        request.target,
        request.migration_authority_ref,
        "import:account:1",
        NOW,
    )


def movement():
    return OpeningCashMovementEvidenceV1(
        "payments", "refund:1", "v1", "b" * 64, "confirmed"
    )


def cash(amount="1.23", precision=2):
    return Money.of(amount, Currency("NGN", precision))


def test_import_command_is_frozen_and_binds_authority_replay_and_source():
    command = import_command()
    digest = opening_cash_command_digest(command)
    with pytest.raises(FrozenInstanceError):
        command.idempotency_key = "changed"
    manifest = command.manifest
    lot = manifest.cash_lots[0]
    variants = (
        replace(command, idempotency_key="other"),
        replace(command, occurred_at=NOW + timedelta(microseconds=1)),
        replace(command, migration_authority_ref="other"),
        replace(command, scope=TenantScope(UUID(int=9))),
        replace(
            command, partition=replace(command.partition, source_account_key="other")
        ),
        replace(
            command,
            target=replace(command.target, expected_position_fingerprint="other"),
        ),
        replace(
            command, target=replace(command.target, posting_watermark="posting:other")
        ),
        replace(
            command,
            manifest=replace(
                manifest, capture=replace(manifest.capture, installation_id="other")
            ),
        ),
        replace(
            command,
            manifest=replace(
                manifest, cash_lots=(replace(lot, source_system="other"),)
            ),
        ),
        replace(
            command,
            manifest=replace(
                manifest, cash_lots=(replace(lot, source_settlement_key="other"),)
            ),
        ),
    )
    assert all(opening_cash_command_digest(item) != digest for item in variants)
    assert opening_cash_command_digest(command) == digest


def test_cash_command_domains_and_full_refund_evidence_do_not_alias():
    scope = import_command().scope
    refund = RecordOpeningCashRefundV1(
        scope, UUID(int=1), cash(), movement(), "refund-auth:1", "refund:1", NOW
    )
    correction = CorrectOpeningCashRefundV1(
        scope, UUID(int=1), cash(), movement(), "refund-auth:1", "refund:1", NOW
    )
    draw = ReverseCashCreditDrawV1(
        scope, UUID(int=1), cash(), "refund-auth:1", "refund:1", NOW
    )
    unwind = ReverseOpeningAccountImportV1(
        scope, UUID(int=1), "refund-auth:1", "refund:1", NOW
    )
    digests = {
        opening_cash_command_digest(item) for item in (refund, correction, draw, unwind)
    }
    assert len(digests) == 4
    for changed in (
        replace(refund, amount=cash("1.24")),
        replace(refund, evidence=replace(refund.evidence, digest="c" * 64)),
        replace(refund, evidence=replace(refund.evidence, reference="refund:2")),
        replace(refund, authorization_ref="refund-auth:2"),
    ):
        assert opening_cash_command_digest(changed) != opening_cash_command_digest(
            refund
        )


@pytest.mark.parametrize(
    "command_type,id_field",
    [
        (RecordOpeningCashRefundV1, "lot_id"),
        (CorrectOpeningCashRefundV1, "refund_effect_id"),
        (ReverseCashCreditDrawV1, "draw_posting_group_id"),
    ],
)
@pytest.mark.parametrize("amount", [None, "1.23", Decimal("1.23"), True])
def test_reversal_and_refund_require_an_explicit_money_amount(
    command_type, id_field, amount
):
    values = {
        "scope": import_command().scope,
        "amount": amount,
        "authorization_ref": "auth:1",
        "idempotency_key": "command:1",
        "occurred_at": NOW,
    }
    values[id_field] = UUID(int=1)
    if command_type is not ReverseCashCreditDrawV1:
        values["evidence"] = movement()
    with pytest.raises(OpeningEvidenceError):
        command_type(**values)


@pytest.mark.parametrize("amount", ["0", "-0.01"])
def test_nonpositive_cash_movements_cannot_be_constructed(amount):
    with pytest.raises(OpeningEvidenceError):
        ReverseCashCreditDrawV1(
            import_command().scope,
            UUID(int=1),
            cash(amount),
            "auth:1",
            "inverse:1",
            NOW,
        )


def test_numeric20_6_boundary_is_exact_and_ambient_decimal_context_is_preserved():
    amount = cash("99999999999999.999999", 6)
    command = ReverseCashCreditDrawV1(
        import_command().scope, UUID(int=1), amount, "auth:1", "inverse:1", NOW
    )
    digest = opening_cash_command_digest(command)
    with localcontext() as ambient:
        ambient.prec = 3
        ambient.Emin, ambient.Emax, ambient.clamp = -2, 2, 1
        for signal in (Inexact, Rounded, Overflow, Underflow, Subnormal, Clamped):
            ambient.traps[signal] = True
        ambient.clear_flags()
        flags, traps = dict(ambient.flags), dict(ambient.traps)
        assert opening_cash_command_digest(command) == digest
        assert replace(command).amount.amount == Decimal("99999999999999.999999")
        assert ambient.prec == 3
        assert dict(ambient.flags) == flags
        assert dict(ambient.traps) == traps
    with pytest.raises(OpeningEvidenceError):
        replace(command, amount=cash("100000000000000", 6))
    with pytest.raises(OpeningEvidenceError):
        replace(command, amount=cash("1", 7))


def test_equal_instant_offsets_have_the_same_command_digest():
    command = import_command()
    equivalent = replace(
        command, occurred_at=NOW.astimezone(timezone(timedelta(hours=1)))
    )
    assert opening_cash_command_digest(equivalent) == opening_cash_command_digest(
        command
    )


def test_cash_source_order_requires_exactly_one_real_backing_effect():
    with pytest.raises(OpeningEvidenceError):
        CashCreditSourceOrderV1(1, None, None)
    with pytest.raises(OpeningEvidenceError):
        CashCreditSourceOrderV1(1, UUID(int=1), UUID(int=2))
    assert CashCreditSourceOrderV1(1, UUID(int=1), None).allocation_effect_id == UUID(
        int=1
    )
    assert CashCreditSourceOrderV1(2, None, UUID(int=2)).opening_effect_id == UUID(
        int=2
    )


def _accounting_v4(*, effects=None, allocation=None):
    identifier = UUID(int=1)
    effect = (
        effects
        if effects is not None
        else (AccountingEffectV1("available_credit", cash("-1.23")),)
    )
    allocation = allocation or AccountingAllocationEffectV1(
        identifier, identifier, "allocation", cash("1.23")
    )
    return AccountingFactV4(
        import_command().scope,
        "dotmac-billing",
        identifier,
        4,
        identifier,
        identifier,
        "draw:1",
        "internal",
        "opening_cash_applied",
        NOW,
        NOW,
        effect,
        (AccountingSettlementAllocationV4(identifier, allocation),),
        (),
        (CashCreditSourceOrderV1(1, identifier, None),),
        None,
        None,
    )


def test_v4_recursively_accepts_real_negative_accounting_effects():
    value = _accounting_v4()
    assert value.effects[0].amount_delta == cash("-1.23")
    assert value.settlement_allocations[0].allocation.amount_delta == cash("1.23")


@pytest.mark.parametrize(
    "effect",
    [
        lambda: AccountingEffectV1("invented_lane", cash()),
        lambda: AccountingEffectV1("available_credit", "1.23"),
        lambda: AccountingEffectV1("available_credit", [cash()]),
        lambda: AccountingEffectV1("available_credit", cash("NaN")),
    ],
)
def test_v4_refuses_genuinely_constructed_malformed_legacy_effect_dtos(effect):
    unchecked_v1 = effect()
    assert isinstance(unchecked_v1, AccountingEffectV1)
    with pytest.raises(OpeningEvidenceError):
        _accounting_v4(effects=(unchecked_v1,))


@pytest.mark.parametrize(
    "allocation",
    [
        lambda: AccountingAllocationEffectV1(
            "not-a-uuid", UUID(int=1), "allocation", cash()
        ),
        lambda: AccountingAllocationEffectV1(
            UUID(int=1), "not-a-uuid", "allocation", cash()
        ),
        lambda: AccountingAllocationEffectV1(
            UUID(int=1), UUID(int=1), "invented_kind", cash()
        ),
        lambda: AccountingAllocationEffectV1(
            UUID(int=1), UUID(int=1), "allocation", cash("NaN")
        ),
    ],
)
def test_v4_refuses_genuinely_constructed_malformed_legacy_allocation_dtos(allocation):
    unchecked_v1 = allocation()
    assert isinstance(unchecked_v1, AccountingAllocationEffectV1)
    with pytest.raises(OpeningEvidenceError):
        _accounting_v4(allocation=unchecked_v1)


def test_v4_closed_ordered_collections_reject_mutable_lists():
    with pytest.raises(OpeningEvidenceError):
        _accounting_v4(effects=[AccountingEffectV1("available_credit", cash())])


def test_movement_amount_validation_does_not_mutate_global_decimal_context():
    original = getcontext().copy()
    amount = cash("123.456789", 6)
    with localcontext() as ambient:
        ambient.prec, ambient.Emin, ambient.Emax, ambient.clamp = 3, -2, 2, 1
        for signal in (Inexact, Rounded, Overflow, Underflow, Subnormal, Clamped):
            ambient.traps[signal] = True
        before = (
            ambient.prec,
            ambient.Emin,
            ambient.Emax,
            ambient.clamp,
            dict(ambient.flags),
            dict(ambient.traps),
        )
        value = ReverseCashCreditDrawV1(
            import_command().scope, UUID(int=1), amount, "authority:1", "inverse:1", NOW
        )
        assert value.amount.amount == Decimal("123.456789")
        assert (
            ambient.prec,
            ambient.Emin,
            ambient.Emax,
            ambient.clamp,
            dict(ambient.flags),
            dict(ambient.traps),
        ) == before
    current = getcontext()
    assert (
        current.prec,
        current.Emin,
        current.Emax,
        current.clamp,
        dict(current.flags),
        dict(current.traps),
    ) == (
        original.prec,
        original.Emin,
        original.Emax,
        original.clamp,
        dict(original.flags),
        dict(original.traps),
    )


@pytest.mark.parametrize("invalid_trap", [True, False])
def test_effect_delta_reconstruction_is_independent_of_ambient_exponent_limits(
    invalid_trap,
):
    amount = cash("12345.678901", 6)
    expected = cash("-12345.678901", 6)
    identifier = UUID(int=1)
    effect = OpeningCashEffectV1(
        import_command().scope,
        identifier,
        identifier,
        identifier,
        identifier,
        1,
        "application",
        amount,
        identifier,
        None,
        "draw:1",
        "authority:1",
        None,
        None,
        "a" * 64,
        NOW,
        NOW,
    )
    with localcontext() as ambient:
        ambient.prec, ambient.Emin, ambient.Emax, ambient.clamp = 3, -2, 2, 1
        for signal in (Inexact, Rounded, Overflow, Underflow, Subnormal, Clamped):
            ambient.traps[signal] = True
        ambient.traps[InvalidOperation] = invalid_trap
        ambient.clear_flags()
        before = (
            ambient.prec,
            ambient.Emin,
            ambient.Emax,
            ambient.clamp,
            dict(ambient.flags),
            dict(ambient.traps),
        )
        delta = effect.availability_delta
        assert delta.amount.is_finite()
        assert delta == expected
        assert delta.amount == Decimal("-12345.678901")
        assert (
            ambient.prec,
            ambient.Emin,
            ambient.Emax,
            ambient.clamp,
            dict(ambient.flags),
            dict(ambient.traps),
        ) == before
