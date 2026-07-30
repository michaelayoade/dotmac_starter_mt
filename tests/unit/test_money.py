"""Unit tests for `dotmac_kernel.money` (kernel WS4) — exact money + FX.

The load-bearing property throughout: no `float`, exact `Decimal` arithmetic,
and distributions that sum back to the original to the last minor unit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

import pytest
from dotmac_kernel import (
    Currency,
    CurrencyMismatchError,
    ExchangeRate,
    Money,
    MoneyError,
    currency,
)

NGN = currency("NGN")
USD = currency("USD")
JPY = currency("JPY")  # 0 minor units


# ── Currency ─────────────────────────────────────────────────────────────────
def test_currency_normalizes_and_validates() -> None:
    assert currency("ngn") == Currency("NGN", 2)
    assert currency("JPY").minor_units == 0
    with pytest.raises(MoneyError):
        Currency("NG")  # too short
    with pytest.raises(MoneyError):
        Currency("N1G")  # not alpha


# ── Money construction / exactness ───────────────────────────────────────────
def test_money_is_quantized_and_never_float() -> None:
    assert Money.of("10.005", NGN).amount == Decimal("10.01")  # half-up
    assert Money.of("10", NGN).amount == Decimal("10.00")
    assert Money.of(1000, JPY).amount == Decimal("1000")  # 0 minor units
    with pytest.raises(MoneyError):
        Money.of(1.1, NGN)  # type: ignore[arg-type]  # float refused


def test_money_zero_and_predicates() -> None:
    z = Money.zero(NGN)
    assert z.is_zero and not z.is_negative and not z.is_positive
    assert Money.of("-1", NGN).is_negative
    assert Money.of("1", NGN).is_positive


# ── arithmetic ───────────────────────────────────────────────────────────────
def test_add_subtract_same_currency() -> None:
    assert Money.of("1.50", NGN) + Money.of("2.25", NGN) == Money.of("3.75", NGN)
    assert Money.of("5", NGN) - Money.of("1.01", NGN) == Money.of("3.99", NGN)
    assert -Money.of("2", NGN) == Money.of("-2", NGN)


def test_currency_mismatch_raises() -> None:
    with pytest.raises(CurrencyMismatchError):
        Money.of("1", NGN) + Money.of("1", USD)
    with pytest.raises(CurrencyMismatchError):
        _ = Money.of("1", NGN) < Money.of("1", USD)


def test_multiply_rounds_the_product_to_minor_units() -> None:
    # 1.00 * 0.125 = 0.125 -> 0.13 (half-up), 0.12 (round-down). The operand
    # amounts are exact; only the PRODUCT needs rounding.
    assert Money.of("1.00", NGN).multiply("0.125") == Money.of("0.13", NGN)
    assert Money.of("1.00", NGN).multiply("0.125", rounding=ROUND_DOWN) == Money.of(
        "0.12", NGN
    )
    assert Money.of("2.00", NGN).multiply("1.5") == Money.of("3.00", NGN)


def test_money_of_quantizes_on_construction() -> None:
    # Money cannot hold sub-minor-unit precision: 0.335 NGN -> 0.34 on build.
    assert Money.of("0.335", NGN).amount == Decimal("0.34")


def test_comparison() -> None:
    assert Money.of("1", NGN) < Money.of("2", NGN)
    assert Money.of("2", NGN) >= Money.of("2", NGN)


# ── exact distribution ───────────────────────────────────────────────────────
def test_allocate_sums_back_exactly_with_remainder_distribution() -> None:
    # 0.10 split 1:1:1 -> 0.04, 0.03, 0.03 (largest fractional parts get the cent)
    parts = Money.of("0.10", NGN).allocate([1, 1, 1])
    assert [p.amount for p in parts] == [
        Decimal("0.04"),
        Decimal("0.03"),
        Decimal("0.03"),
    ]
    assert sum((p.amount for p in parts), Decimal(0)) == Decimal("0.10")


def test_allocate_weighted_and_sums_back() -> None:
    parts = Money.of("100.00", NGN).allocate([1, 3])  # 25 / 75
    assert [p.amount for p in parts] == [Decimal("25.00"), Decimal("75.00")]
    total = sum((p.amount for p in parts), Decimal(0))
    assert total == Decimal("100.00")


def test_split_is_exact() -> None:
    parts = Money.of("10.00", NGN).split(3)
    assert sum((p.amount for p in parts), Decimal(0)) == Decimal("10.00")
    assert parts[0] == Money.of("3.34", NGN)  # remainder to the first
    assert parts[1] == parts[2] == Money.of("3.33", NGN)


def test_allocate_rejects_bad_weights() -> None:
    with pytest.raises(MoneyError):
        Money.of("1", NGN).allocate([])
    with pytest.raises(MoneyError):
        Money.of("1", NGN).allocate([0, 0])
    with pytest.raises(MoneyError):
        Money.of("1", NGN).allocate([-1, 2])


# ── immutability ─────────────────────────────────────────────────────────────
def test_money_is_frozen() -> None:
    import dataclasses

    m = Money.of("1", NGN)
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.amount = Decimal("2")  # type: ignore[misc]


# ── ExchangeRate ─────────────────────────────────────────────────────────────
def test_exchange_rate_converts_and_rounds_to_quote() -> None:
    rate = ExchangeRate(
        base=USD,
        quote=NGN,
        rate=Decimal("1600.50"),
        as_of=datetime(2026, 7, 30, tzinfo=UTC),
        source="test",
    )
    converted = rate.convert(Money.of("10.00", USD))
    assert converted == Money.of("16005.00", NGN)
    assert converted.currency == NGN


def test_exchange_rate_rejects_wrong_base_currency() -> None:
    rate = ExchangeRate(
        base=USD,
        quote=NGN,
        rate=Decimal("1600"),
        as_of=datetime(2026, 7, 30, tzinfo=UTC),
        source="t",
    )
    with pytest.raises(CurrencyMismatchError):
        rate.convert(Money.of("10", NGN))  # NGN is not the base


def test_exchange_rate_validates() -> None:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    with pytest.raises(MoneyError):
        ExchangeRate(base=USD, quote=NGN, rate=Decimal("0"), as_of=now, source="t")
    with pytest.raises(MoneyError):
        ExchangeRate(base=USD, quote=USD, rate=Decimal("1"), as_of=now, source="t")
