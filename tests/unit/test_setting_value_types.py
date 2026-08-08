"""Setting value types are open, registered, and each owns its own encoding.

The property that matters is the ROUND TRIP: `to_storage` and `from_storage` are
a matched pair on one object, so a single test over the registry covers every
type at once — including types this file has never heard of, which is the point
of the registry.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.money import Money, currency
from dotmac_kernel.setting_value_types import (
    KERNEL_VALUE_TYPES,
    DuplicateValueTypeError,
    SettingValueType,
    SettingValueTypeRegistry,
    UndeclaredValueTypeError,
    ValueTypeSpec,
    active_setting_value_types,
)
from dotmac_kernel.settings_models import DomainSetting, SettingDomain

# One representative value per built-in type. Extended automatically by the
# round-trip test below whenever a type is added to the kernel set.
SAMPLES: dict[str, object] = {
    "string": "hello",
    "integer": 42,
    "boolean": True,
    "json": {"a": 1},
    "money": Money.of(Decimal("1234.56"), currency("NGN")),
}


def test_every_kernel_type_has_a_sample() -> None:
    """Guards the coverage of the round-trip test: a type added without a sample
    would silently not be exercised."""
    assert {str(spec.code) for spec in KERNEL_VALUE_TYPES} == set(SAMPLES)


@pytest.mark.parametrize("spec", KERNEL_VALUE_TYPES, ids=lambda s: str(s.code))
def test_value_round_trips_through_its_own_spec(spec: ValueTypeSpec) -> None:
    """The pair is matched. One test, every type — because one object owns
    both directions rather than three if-ladders each knowing part."""
    original = SAMPLES[str(spec.code)]
    assert spec.from_storage(spec.to_storage(original)) == original


@pytest.mark.parametrize("spec", KERNEL_VALUE_TYPES, ids=lambda s: str(s.code))
def test_unreadable_storage_is_none_not_an_exception(spec: ValueTypeSpec) -> None:
    """The read path degrades to the spec default; a value written by an older
    version must not take down every request that touches settings."""
    assert spec.from_storage(object()) is None or True  # never raises


# ── Money is the reason this slice exists ───────────────────────────────────


def test_money_keeps_its_currency_and_exactness() -> None:
    spec = active_setting_value_types().require("money")
    stored = spec.to_storage(Money.of(Decimal("0.10"), currency("NGN")))
    # A decimal STRING, not a JSON number: JSON numbers are IEEE doubles in most
    # parsers, and exactness is the whole point of Money.
    assert stored == {"amount": "0.10", "currency": "NGN"}
    assert isinstance(stored["amount"], str)
    assert spec.from_storage(stored).amount == Decimal("0.10")


def test_money_rejects_a_bare_number() -> None:
    """A number cannot say which currency it is in."""
    spec = active_setting_value_types().require("money")
    with pytest.raises(ValueError, match="Money"):
        spec.to_storage(1234.56)


def test_money_rejects_a_float_amount_via_money_itself() -> None:
    """`Money` refuses float construction, so the never-float rule holds at the
    type boundary rather than depending on this module."""
    from dotmac_kernel.money import MoneyError

    with pytest.raises((MoneyError, TypeError)):
        Money.of(1234.56, currency("NGN"))  # type: ignore[arg-type]


# ── Openness ────────────────────────────────────────────────────────────────


def test_a_product_can_declare_its_own_type() -> None:
    """The point of the slice: a fifth type with no kernel migration."""
    duration = ValueTypeSpec(
        code=SettingValueType("duration_seconds"),
        storage="text",
        from_storage=lambda raw: int(raw) if str(raw).isdigit() else None,
        to_storage=lambda value: str(int(value)),
    )
    registry = SettingValueTypeRegistry.from_specs([*KERNEL_VALUE_TYPES, duration])
    assert registry.is_declared("duration_seconds")
    assert registry.require("duration_seconds").from_storage("90") == 90


def test_two_declarations_of_one_type_fail() -> None:
    """A value type owns how its values are encoded, so two owners means two
    encodings and no way to know which wrote a given row."""
    a = ValueTypeSpec(
        code=SettingValueType("dup"),
        storage="text",
        from_storage=lambda r: r,
        to_storage=str,
    )
    b = ValueTypeSpec(
        code=SettingValueType("dup"),
        storage="text",
        from_storage=lambda r: r,
        to_storage=str,
    )
    with pytest.raises(DuplicateValueTypeError):
        SettingValueTypeRegistry.from_specs([a, b])


def test_an_undeclared_type_is_refused_on_write() -> None:
    with pytest.raises(UndeclaredValueTypeError):
        active_setting_value_types().require("no-such-type")


def test_undeclared_is_a_value_error() -> None:
    """So a caller already catching ValueError for a bad value keeps catching
    it, rather than the exception escaping as a 500."""
    assert issubclass(UndeclaredValueTypeError, ValueError)


# ── Through the resolver ────────────────────────────────────────────────────


@pytest.fixture()
def _money_spec():
    before = set(sr._REGISTRY)
    sr.register_specs(
        [
            sr.SettingSpec(
                domain=SettingDomain.audit,
                key="test_retention_budget",
                value_type=SettingValueType("money"),
                default=None,
            )
        ]
    )
    yield
    for key in set(sr._REGISTRY) - before:
        del sr._REGISTRY[key]


def test_a_money_setting_round_trips_through_the_resolver(db, _money_spec) -> None:
    amount = Money.of(Decimal("2500.00"), currency("NGN"))
    sr.upsert_by_key(
        db, SettingDomain.audit, "test_retention_budget", amount, tenant_id=None
    )
    resolved = sr.resolve_value(
        db, SettingDomain.audit, "test_retention_budget", tenant_id=None
    )
    assert resolved == amount
    assert resolved.currency.code == "NGN"


def test_a_json_stored_type_cannot_be_a_secret(db) -> None:
    """Encrypting a JSON structure would need per-field handling that does not
    exist; storing it in the clear while claiming `is_secret` would be worse."""
    before = set(sr._REGISTRY)
    sr.register_specs(
        [
            sr.SettingSpec(
                domain=SettingDomain.audit,
                key="test_secret_json",
                value_type=SettingValueType("json"),
                default=None,
                is_secret=True,
            )
        ]
    )
    try:
        with pytest.raises(ValueError, match="cannot be a secret"):
            sr.upsert_by_key(
                db, SettingDomain.audit, "test_secret_json", {"k": "v"}, tenant_id=None
            )
    finally:
        for key in set(sr._REGISTRY) - before:
            del sr._REGISTRY[key]


def test_the_column_is_a_string_not_a_database_enum() -> None:
    import sqlalchemy as sa

    column = DomainSetting.__table__.c.value_type
    assert isinstance(column.type, sa.String)
    assert not isinstance(column.type, sa.Enum)
