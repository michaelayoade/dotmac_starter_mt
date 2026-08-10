"""Exact money + FX primitives (kernel WS4).

Value objects for money handling that never touch `float` (ADR-0003: "exact
Money (never float), immutable FX/price/rating/tax-policy snapshots"):

- `Currency` — an ISO-4217 code + its minor-unit exponent (decimal places).
- `Money` — a currency + an exact `Decimal` amount, quantized to the currency's
  minor units. Frozen; arithmetic returns new `Money`; mixing currencies raises.
- `ExchangeRate` — an immutable, timestamped, sourced rate snapshot; `convert`
  applies it with an explicit rounding mode.

Pure values — no DB, no I/O, no network — so this is import-safe and re-exported
at the top level. A request-time access check never converts by calling a
provider; it uses a pre-fetched immutable `ExchangeRate` snapshot.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# What a Money amount may be constructed from — never `float` (inexact).
Amountable = int | str | Decimal

# Default money rounding: round-half-up (the common commercial convention). Every
# operation that can lose precision takes an explicit `rounding` so a caller can
# choose banker's rounding etc.; this is only the default.
DEFAULT_ROUNDING = ROUND_HALF_UP


class MoneyError(Exception):
    """Base for money/FX misuse (a programming/value error, not a domain 4xx)."""


class CurrencyMismatchError(MoneyError):
    """Raised when an operation mixes two different currencies."""


@dataclass(frozen=True, slots=True)
class Currency:
    """An ISO-4217 currency: its `code` and `minor_units` (fraction digits — 2
    for NGN/USD/EUR, 0 for JPY, 3 for BHD)."""

    code: str
    minor_units: int = 2

    def __post_init__(self) -> None:
        ok = isinstance(self.code, str) and len(self.code) == 3 and self.code.isalpha()
        if not ok:
            raise MoneyError(f"invalid ISO-4217 code: {self.code!r}")
        if self.minor_units < 0:
            raise MoneyError(f"minor_units must be >= 0, got {self.minor_units}")
        object.__setattr__(self, "code", self.code.upper())

    @property
    def _quantum(self) -> Decimal:
        return Decimal(1).scaleb(-self.minor_units)  # e.g. 2 -> 0.01


# A small registry of the currencies the fleet uses; extend as needed. Unknown
# codes are still constructable via `Currency(code, minor_units)` — this is a
# convenience lookup, not an allowlist.
_REGISTRY: dict[str, Currency] = {
    c.code: c
    for c in (
        Currency("NGN", 2),
        Currency("USD", 2),
        Currency("EUR", 2),
        Currency("GBP", 2),
        Currency("JPY", 0),
    )
}


def currency(code: str) -> Currency:
    """Look up a registered `Currency` by code, else a 2-minor-unit default.
    Construct `Currency(code, minor_units)` directly for a non-2-digit currency
    not in the registry."""
    return _REGISTRY.get(code.upper(), Currency(code, 2))


@dataclass(frozen=True, slots=True)
class Money:
    """An exact monetary amount in a single `Currency`. The `amount` is always
    quantized to the currency's minor units. Immutable — every operation returns
    a new `Money`."""

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", self._quantize(self.amount))

    # ── construction ─────────────────────────────────────────────────────────
    @classmethod
    def of(
        cls, amount: Amountable, currency: Currency, *, rounding: str = DEFAULT_ROUNDING
    ) -> Money:
        """Build `Money` from an int / str / Decimal (never float), rounding to
        the currency's minor units with `rounding`."""
        if isinstance(amount, float):  # pragma: no cover - guarded for safety
            raise MoneyError("refusing to build Money from float; use str/Decimal")
        try:
            dec = Decimal(amount)
        except (InvalidOperation, ValueError) as exc:
            raise MoneyError(f"not a valid amount: {amount!r}") from exc
        quantum = currency._quantum
        return cls(dec.quantize(quantum, rounding=rounding), currency)

    @classmethod
    def zero(cls, currency: Currency) -> Money:
        return cls(Decimal(0), currency)

    # ── internals ─────────────────────────────────────────────────────────────
    def _quantize(self, value: Decimal, *, rounding: str = DEFAULT_ROUNDING) -> Decimal:
        return value.quantize(self.currency._quantum, rounding=rounding)

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"{self.currency.code} vs {other.currency.code}"
            )

    # ── arithmetic (returns new Money) ─────────────────────────────────────────
    def add(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def subtract(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def multiply(
        self, factor: Amountable, *, rounding: str = DEFAULT_ROUNDING
    ) -> Money:
        """Scale by an exact factor (int/str/Decimal), rounding the product to
        the currency's minor units."""
        if isinstance(factor, float):  # pragma: no cover
            raise MoneyError("refusing to multiply Money by float; use str/Decimal")
        product = self.amount * Decimal(factor)
        return Money(self._quantize(product, rounding=rounding), self.currency)

    def __add__(self, other: Money) -> Money:
        return self.add(other)

    def __sub__(self, other: Money) -> Money:
        return self.subtract(other)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    # ── comparison ─────────────────────────────────────────────────────────────
    def _cmp_key(self, other: Money) -> Decimal:
        self._same_currency(other)
        return other.amount

    def __lt__(self, other: Money) -> bool:
        return self.amount < self._cmp_key(other)

    def __le__(self, other: Money) -> bool:
        return self.amount <= self._cmp_key(other)

    def __gt__(self, other: Money) -> bool:
        return self.amount > self._cmp_key(other)

    def __ge__(self, other: Money) -> bool:
        return self.amount >= self._cmp_key(other)

    # ── predicates ─────────────────────────────────────────────────────────────
    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    @property
    def is_positive(self) -> bool:
        return self.amount > 0

    # ── exact distribution (never loses or invents a minor unit) ───────────────
    def allocate(self, weights: Sequence[Amountable]) -> list[Money]:
        """Split this amount across `weights` proportionally, distributing the
        rounding remainder one minor unit at a time (largest-first) so the parts
        sum EXACTLY back to the original — the standard billing split that never
        loses or creates a cent."""
        if not weights:
            raise MoneyError("allocate needs at least one weight")
        decs = [Decimal(w) for w in weights]
        if any(w < 0 for w in decs):
            raise MoneyError("allocate weights must be non-negative")
        total = sum(decs)
        if total <= 0:
            raise MoneyError("allocate weights must sum to a positive value")

        quantum = self.currency._quantum
        # Work in integer minor units to make the remainder distribution exact.
        total_units = int((self.amount / quantum).to_integral_value())
        raw = [total_units * w / total for w in decs]
        floors = [int(r.to_integral_value(rounding="ROUND_FLOOR")) for r in raw]
        remainder = total_units - sum(floors)
        # Hand the leftover units to the largest fractional parts first.
        order = sorted(range(len(decs)), key=lambda i: raw[i] - floors[i], reverse=True)
        for k in range(remainder):
            floors[order[k]] += 1
        return [Money(Decimal(u) * quantum, self.currency) for u in floors]

    def split(self, n: int) -> list[Money]:
        """Split into `n` as-equal-as-possible parts that sum EXACTLY back."""
        if n <= 0:
            raise MoneyError("split n must be >= 1")
        return self.allocate([1] * n)

    # ── display ────────────────────────────────────────────────────────────────
    def __str__(self) -> str:
        return f"{self.amount} {self.currency.code}"


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    """An immutable FX-rate snapshot: convert `base` → `quote` at `rate`, as
    observed `as_of` from `source`. Passed to `convert`; never derived by calling
    a live provider at access time."""

    base: Currency
    quote: Currency
    rate: Decimal
    as_of: datetime
    source: str
    rate_id: str | None = field(default=None)

    def __post_init__(self) -> None:
        if not isinstance(self.rate, Decimal):
            object.__setattr__(self, "rate", Decimal(self.rate))
        if self.rate <= 0:
            raise MoneyError(f"exchange rate must be positive, got {self.rate}")
        if self.base == self.quote:
            raise MoneyError("base and quote currencies must differ")

    def convert(self, money: Money, *, rounding: str = DEFAULT_ROUNDING) -> Money:
        """Convert `money` (which must be in `base`) into `quote`, rounding to
        the quote currency's minor units."""
        if money.currency != self.base:
            raise CurrencyMismatchError(
                f"rate is {self.base.code}->{self.quote.code}, "
                f"cannot convert {money.currency.code}"
            )
        converted = money.amount * self.rate
        return Money.of(converted, self.quote, rounding=rounding)


__all__ = [
    "DEFAULT_ROUNDING",
    "Amountable",
    "Currency",
    "CurrencyMismatchError",
    "ExchangeRate",
    "Money",
    "MoneyError",
    "currency",
]
