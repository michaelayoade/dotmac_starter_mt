"""Pure contract proofs for the reusable Orders owner (ADR-0030 §5b)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from dotmac_kernel.money import Money, currency
from dotmac_orders import (
    DEFAULT_ORDER_STATES,
    FulfillmentEligibilityDecisionV1,
    LineInput,
    OrderError,
    OrderPhase,
    OrderStateRegistry,
    OrderStateSpec,
    TaxSnapshotV1,
    TermsSnapshotV1,
    TermValueV1,
    calculate_line_snapshot,
    calculate_order_totals,
    evaluate_fulfillment_eligibility,
    snapshot_fingerprint,
)

NGN = currency("NGN")


def _line(**overrides: object) -> LineInput:
    values: dict[str, object] = {
        "line_key": "line-1",
        "description": "Managed service",
        "quantity": Decimal("2.5"),
        "unit_price": Money.of("100", NGN),
        "discount": Money.of("10", NGN),
        "taxes": (
            TaxSnapshotV1(
                tax_code="vat",
                source_version="tax-policy-4",
                taxable_basis=Money.of("100", NGN),
                rate=Decimal("0.075"),
                amount=Money.of("7.50", NGN),
            ),
        ),
        "price_version_ref": "price-version-7",
        "terms_ref": "terms-3",
        "terms_snapshot": TermsSnapshotV1(
            version_ref="terms-3",
            values=(TermValueV1(name="minimum_term_months", value="12"),),
        ),
        "specification_ref": "service-spec-42",
    }
    values.update(overrides)
    return LineInput(**values)  # type: ignore[arg-type]


def test_line_total_is_derived_exactly_from_the_frozen_inputs() -> None:
    line = calculate_line_snapshot(_line())

    assert line.extended_price == Money.of("250", NGN)
    assert line.total == Money.of("247.50", NGN)
    assert line.price_version_ref == "price-version-7"
    assert line.specification_ref == "service-spec-42"


def test_floats_are_refused_at_the_commercial_boundary() -> None:
    with pytest.raises(OrderError) as exc:
        calculate_line_snapshot(_line(quantity=2.5))  # type: ignore[arg-type]

    assert exc.value.code == "invalid_quantity"


def test_negative_or_zero_quantities_and_negative_components_fail_closed() -> None:
    for line in (
        _line(quantity=Decimal("0")),
        _line(quantity=Decimal("-1")),
        _line(discount=Money.of("-0.01", NGN)),
        _line(
            taxes=(
                TaxSnapshotV1(
                    tax_code="vat",
                    source_version="tax-policy-4",
                    taxable_basis=Money.of("100", NGN),
                    rate=Decimal("0.075"),
                    amount=Money.of("-0.01", NGN),
                ),
            )
        ),
    ):
        with pytest.raises(OrderError):
            calculate_line_snapshot(line)


def test_values_outside_the_persisted_numeric_contract_are_refused() -> None:
    for line in (
        _line(quantity=Decimal("0.0000001")),
        _line(quantity=Decimal("100000000000000")),
        _line(unit_price=Money.of("100000000000000", NGN)),
    ):
        with pytest.raises(OrderError) as exc:
            calculate_line_snapshot(line)
        assert exc.value.code in {"quantity_out_of_range", "money_out_of_range"}


def test_every_line_requires_price_terms_and_specification_provenance() -> None:
    for field in ("price_version_ref", "terms_ref", "specification_ref"):
        with pytest.raises(OrderError) as exc:
            calculate_line_snapshot(_line(**{field: ""}))
        assert exc.value.code == "missing_snapshot_provenance"


def test_optional_line_source_provenance_is_an_atomic_pair() -> None:
    for line in (
        _line(source_ref="checkout:1"),
        _line(source_version="accepted:7"),
    ):
        with pytest.raises(OrderError) as exc:
            calculate_line_snapshot(line)
        assert exc.value.code == "invalid_source_provenance"


def test_terms_content_must_match_the_declared_immutable_version() -> None:
    with pytest.raises(OrderError) as exc:
        calculate_line_snapshot(
            _line(
                terms_snapshot=TermsSnapshotV1(
                    version_ref="terms-other",
                    values=(TermValueV1(name="minimum_term_months", value="12"),),
                )
            )
        )
    assert exc.value.code == "terms_snapshot_version_mismatch"


def test_order_totals_are_the_sum_of_immutable_line_snapshots() -> None:
    first = calculate_line_snapshot(_line())
    second = calculate_line_snapshot(
        _line(
            line_key="line-2",
            quantity=Decimal("1"),
            unit_price=Money.of("50", NGN),
            discount=Money.of("0", NGN),
            taxes=(
                TaxSnapshotV1(
                    tax_code="vat",
                    source_version="tax-policy-4",
                    taxable_basis=Money.of("50", NGN),
                    rate=Decimal("0.075"),
                    amount=Money.of("3.75", NGN),
                ),
            ),
        )
    )

    totals = calculate_order_totals((first, second))

    assert totals.subtotal == Money.of("300", NGN)
    assert totals.discount == Money.of("10", NGN)
    assert totals.tax == Money.of("11.25", NGN)
    assert totals.total == Money.of("301.25", NGN)


def test_a_snapshot_is_frozen_and_its_fingerprint_includes_version_identity() -> None:
    line = calculate_line_snapshot(_line())
    original = snapshot_fingerprint((line,))

    with pytest.raises(FrozenInstanceError):
        line.description = "rewritten"  # type: ignore[misc]

    other_version = calculate_line_snapshot(_line(price_version_ref="price-version-8"))
    assert snapshot_fingerprint((other_version,)) != original


def test_order_states_are_registered_strings_not_a_closed_status_enum() -> None:
    registry = OrderStateRegistry(DEFAULT_ORDER_STATES)
    registry.register(
        OrderStateSpec(
            code="awaiting_manual_review",
            phase=OrderPhase.SUBMITTED,
            transitions_to=frozenset({"accepted", "cancelled"}),
        )
    )

    assert registry.transition("awaiting_manual_review", "accepted") == "accepted"


def test_an_undeclared_state_or_transition_is_refused_by_default() -> None:
    registry = OrderStateRegistry(DEFAULT_ORDER_STATES)

    with pytest.raises(OrderError) as unknown:
        registry.transition("submitted", "invented")
    assert unknown.value.code == "undeclared_order_state"

    with pytest.raises(OrderError) as backwards:
        registry.transition("accepted", "submitted")
    assert backwards.value.code == "order_transition_refused"


def test_orders_owns_the_reasoned_fulfillment_eligibility_decision() -> None:
    partial = evaluate_fulfillment_eligibility(
        requirement_refs=("settlement:quote-7", "credit:quote-7"),
        satisfied_requirement_refs=("credit:quote-7",),
    )

    assert partial == FulfillmentEligibilityDecisionV1(
        eligible=False,
        reason_code="eligibility_requirements_missing",
        requirement_refs=("credit:quote-7", "settlement:quote-7"),
        satisfied_requirement_refs=("credit:quote-7",),
        missing_requirement_refs=("settlement:quote-7",),
    )

    complete = evaluate_fulfillment_eligibility(
        requirement_refs=("settlement:quote-7", "credit:quote-7"),
        satisfied_requirement_refs=("settlement:quote-7", "credit:quote-7"),
    )

    assert complete.eligible
    assert complete.reason_code == "eligibility_requirements_satisfied"
    assert complete.missing_requirement_refs == ()


def test_eligibility_fails_closed_for_an_empty_or_unregistered_evidence_set() -> None:
    empty = evaluate_fulfillment_eligibility(
        requirement_refs=(), satisfied_requirement_refs=()
    )
    unknown = evaluate_fulfillment_eligibility(
        requirement_refs=("settlement:quote-7",),
        satisfied_requirement_refs=("balance-derived:quote-7",),
    )

    assert not empty.eligible
    assert empty.reason_code == "eligibility_requirements_not_registered"
    assert not unknown.eligible
    assert unknown.reason_code == "unregistered_eligibility_evidence"
