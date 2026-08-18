"""Persistence-free exact-money and lifecycle decisions for Orders."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum

from dotmac_kernel.money import CurrencyMismatchError, Money

from dotmac_orders.contracts import LineInput, LineSnapshot, OrderTotals
from dotmac_orders.errors import OrderError


def _fingerprint(payload: object) -> str:
    # ``dotmac_kernel.idempotency`` reaches the kernel transaction owner, which
    # constructs an engine at import time. Keep that cost behind the operation
    # boundary so importing this wheel or its manifest needs no DATABASE_URL.
    from dotmac_kernel.idempotency import fingerprint_of

    return fingerprint_of(payload)


class OrderPhase(StrEnum):
    """Closed semantic phases; persisted state codes remain an open registry."""

    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    COVERED = "covered"
    FULFILLMENT = "fulfillment"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class OrderStateSpec:
    code: str
    phase: OrderPhase
    transitions_to: frozenset[str]


class OrderStateRegistry:
    """Open declaration registry for persisted order state codes."""

    def __init__(self, declarations: Iterable[OrderStateSpec] = ()) -> None:
        self._states: dict[str, OrderStateSpec] = {}
        for declaration in declarations:
            self.register(declaration)

    def register(self, declaration: OrderStateSpec) -> None:
        code = declaration.code.strip()
        if not code or code != declaration.code:
            raise OrderError(
                "invalid_order_state",
                "Order state codes must be non-empty and trimmed.",
            )
        if code in self._states:
            raise OrderError(
                "duplicate_order_state",
                f"Order state {code!r} is already declared.",
            )
        self._states[code] = declaration

    def require(self, code: str) -> OrderStateSpec:
        try:
            return self._states[code]
        except KeyError as exc:
            raise OrderError(
                "undeclared_order_state",
                f"Order state {code!r} is not declared.",
            ) from exc

    def transition(self, current: str, target: str) -> str:
        source = self.require(current)
        self.require(target)
        if target not in source.transitions_to:
            raise OrderError(
                "order_transition_refused",
                f"Order cannot transition from {current!r} to {target!r}.",
                details={"current": current, "target": target},
            )
        return target


DEFAULT_ORDER_STATES: tuple[OrderStateSpec, ...] = (
    OrderStateSpec(
        code="submitted",
        phase=OrderPhase.SUBMITTED,
        transitions_to=frozenset({"accepted", "cancelled"}),
    ),
    OrderStateSpec(
        code="accepted",
        phase=OrderPhase.ACCEPTED,
        transitions_to=frozenset({"covered", "cancelled"}),
    ),
    OrderStateSpec(
        code="covered",
        phase=OrderPhase.COVERED,
        transitions_to=frozenset({"fulfillment_requested", "cancelled"}),
    ),
    OrderStateSpec(
        code="fulfillment_requested",
        phase=OrderPhase.FULFILLMENT,
        transitions_to=frozenset({"cancelled"}),
    ),
    OrderStateSpec(
        code="cancelled",
        phase=OrderPhase.TERMINAL,
        transitions_to=frozenset(),
    ),
)


def default_order_state_registry() -> OrderStateRegistry:
    return OrderStateRegistry(DEFAULT_ORDER_STATES)


def _required(value: str, *, field: str) -> str:
    if not value or not value.strip():
        raise OrderError(
            "missing_snapshot_provenance",
            f"{field} is required on every accepted line snapshot.",
            details={"field": field},
        )
    return value


def _same_currency(line: LineInput) -> None:
    expected = line.unit_price.currency
    values = [("discount", line.discount)]
    for tax in line.taxes:
        values.extend(
            (
                (f"tax {tax.tax_code!r} basis", tax.taxable_basis),
                (f"tax {tax.tax_code!r} amount", tax.amount),
            )
        )
    for name, value in values:
        if value.currency != expected:
            raise OrderError(
                "line_currency_mismatch",
                f"{name} must use the line unit-price currency.",
            )


def fits_numeric(value: Decimal, *, precision: int = 20, scale: int = 6) -> bool:
    normalized = value.normalize()
    integer_digits = max(0, normalized.adjusted() + 1)
    exponent = normalized.as_tuple().exponent
    fractional_digits = 0 if isinstance(exponent, str) else max(0, -exponent)
    return integer_digits <= precision - scale and fractional_digits <= scale


def calculate_line_snapshot(line: LineInput) -> LineSnapshot:
    """Validate and freeze the exact commercial values for one accepted line."""

    if isinstance(line.quantity, float) or not isinstance(line.quantity, Decimal):
        raise OrderError(
            "invalid_quantity",
            "Line quantity must be an exact Decimal, never float.",
        )
    if not line.quantity.is_finite() or line.quantity <= 0:
        raise OrderError(
            "invalid_quantity",
            "Line quantity must be finite and positive.",
        )
    if not fits_numeric(line.quantity):
        raise OrderError(
            "quantity_out_of_range",
            "Line quantity must fit the persisted NUMERIC(20,6) contract.",
        )
    if not line.line_key.strip() or not line.description.strip():
        raise OrderError("invalid_line", "Line key and description are required.")
    _same_currency(line)
    if line.unit_price.is_negative or line.discount.is_negative:
        raise OrderError(
            "negative_line_component",
            "Unit price and discount cannot be negative.",
        )
    price_version_ref = _required(
        line.price_version_ref, field="price_version_ref"
    )
    terms_ref = _required(line.terms_ref, field="terms_ref")
    if line.terms_snapshot.version_ref != terms_ref:
        raise OrderError(
            "terms_snapshot_version_mismatch",
            "The captured terms content must name the same immutable version "
            "as terms_ref.",
        )
    if not line.terms_snapshot.values:
        raise OrderError(
            "empty_terms_snapshot",
            "The accepted line must carry the terms content in force.",
        )
    term_names: set[str] = set()
    for term in line.terms_snapshot.values:
        if not term.name.strip() or not term.value.strip():
            raise OrderError(
                "invalid_terms_snapshot",
                "Terms snapshot names and values must be non-empty.",
            )
        if term.name in term_names:
            raise OrderError(
                "duplicate_term_name",
                f"Term {term.name!r} appears more than once in the snapshot.",
            )
        term_names.add(term.name)
    specification_ref = _required(
        line.specification_ref, field="specification_ref"
    )
    if (line.source_ref is None) != (line.source_version is None) or any(
        value is not None and not value.strip()
        for value in (line.source_ref, line.source_version)
    ):
        raise OrderError(
            "invalid_source_provenance",
            "Line source_ref and source_version must be supplied together.",
        )
    tax = Money.zero(line.unit_price.currency)
    tax_keys: set[tuple[str, str]] = set()
    for component in line.taxes:
        tax_code = _required(component.tax_code, field="tax_code")
        source_version = _required(
            component.source_version, field="tax_source_version"
        )
        key = (tax_code, source_version)
        if key in tax_keys:
            raise OrderError(
                "duplicate_tax_snapshot",
                f"Tax snapshot {key!r} appears more than once on the line.",
            )
        tax_keys.add(key)
        if component.taxable_basis.is_negative or component.amount.is_negative:
            raise OrderError(
                "negative_line_component",
                "Tax basis and amount cannot be negative.",
            )
        if component.rate is not None and (
            isinstance(component.rate, float)
            or not isinstance(component.rate, Decimal)
            or not component.rate.is_finite()
            or component.rate < 0
        ):
            raise OrderError(
                "invalid_tax_rate",
                "A captured tax rate must be an exact non-negative Decimal.",
            )
        tax = tax.add(component.amount)
    extended = line.unit_price.multiply(line.quantity)
    total = extended.subtract(line.discount).add(tax)
    if total.is_negative:
        raise OrderError("negative_line_total", "A line total cannot be negative.")
    for name, money in (
        ("unit_price", line.unit_price),
        ("extended_price", extended),
        ("discount", line.discount),
        ("tax", tax),
        ("total", total),
    ):
        if not fits_numeric(money.amount):
            raise OrderError(
                "money_out_of_range",
                f"{name} must fit the persisted NUMERIC(20,6) contract.",
            )
    payload = {
        "line_key": line.line_key,
        "description": line.description,
        "quantity": str(line.quantity),
        "unit_price": str(line.unit_price.amount),
        "currency": line.unit_price.currency.code,
        "discount": str(line.discount.amount),
        "tax": str(tax.amount),
        "taxes": [
            {
                "tax_code": component.tax_code,
                "source_version": component.source_version,
                "taxable_basis": str(component.taxable_basis.amount),
                "rate": (
                    str(component.rate) if component.rate is not None else None
                ),
                "amount": str(component.amount.amount),
            }
            for component in line.taxes
        ],
        "total": str(total.amount),
        "price_version_ref": price_version_ref,
        "terms_ref": terms_ref,
        "terms_snapshot": {
            "version_ref": line.terms_snapshot.version_ref,
            "values": [asdict(term) for term in line.terms_snapshot.values],
        },
        "specification_ref": specification_ref,
        "source_ref": line.source_ref,
        "source_version": line.source_version,
    }
    return LineSnapshot(
        line_key=line.line_key,
        description=line.description,
        quantity=line.quantity,
        unit_price=line.unit_price,
        extended_price=extended,
        discount=line.discount,
        tax=tax,
        taxes=line.taxes,
        total=total,
        price_version_ref=price_version_ref,
        terms_ref=terms_ref,
        terms_snapshot=line.terms_snapshot,
        specification_ref=specification_ref,
        source_ref=line.source_ref,
        source_version=line.source_version,
        fingerprint=_fingerprint(payload),
    )


def calculate_order_totals(lines: Sequence[LineSnapshot]) -> OrderTotals:
    if not lines:
        raise OrderError("empty_order", "An order must contain at least one line.")
    currency = lines[0].unit_price.currency
    subtotal = Money.zero(currency)
    discount = Money.zero(currency)
    tax = Money.zero(currency)
    total = Money.zero(currency)
    keys: set[str] = set()
    for line in lines:
        if line.line_key in keys:
            raise OrderError(
                "duplicate_line_key",
                f"Line key {line.line_key!r} appears more than once.",
            )
        keys.add(line.line_key)
        try:
            subtotal = subtotal.add(line.extended_price)
            discount = discount.add(line.discount)
            tax = tax.add(line.tax)
            total = total.add(line.total)
        except CurrencyMismatchError as exc:
            raise OrderError(
                "order_currency_mismatch",
                "Every line in an order must use one currency.",
            ) from exc
    if subtotal.subtract(discount).add(tax) != total:
        raise OrderError("totals_mismatch", "Derived order totals do not balance.")
    return OrderTotals(subtotal=subtotal, discount=discount, tax=tax, total=total)


def snapshot_fingerprint(lines: Sequence[LineSnapshot]) -> str:
    if not lines:
        raise OrderError("empty_order", "An order must contain at least one line.")
    payload = []
    for line in lines:
        values = asdict(line)
        for name in ("unit_price", "extended_price", "discount", "tax", "total"):
            money = getattr(line, name)
            values[name] = {
                "amount": str(money.amount),
                "currency": money.currency.code,
                "minor_units": money.currency.minor_units,
            }
        values["quantity"] = str(line.quantity)
        payload.append(values)
    return _fingerprint(payload)


__all__ = [
    "DEFAULT_ORDER_STATES",
    "OrderPhase",
    "OrderStateRegistry",
    "OrderStateSpec",
    "calculate_line_snapshot",
    "calculate_order_totals",
    "default_order_state_registry",
    "snapshot_fingerprint",
]
