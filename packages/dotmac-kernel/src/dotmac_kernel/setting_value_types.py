"""Setting value types — open, registered, and each one the sole authority on
how its values are stored and read back.

`SettingValueType` was a four-member enum (`string`, `integer`, `boolean`,
`json`) behind a CHECK constraint. That is the same closed-list defect
`SettingDomain` had, one class away, and it bites hardest against the Money
rule: ADR-0003 requires exact `Money`, never float, and there was no money value
type — so a currency setting had to be a string every reader re-parsed, with the
currency declared nowhere.

## The source-of-truth problem this fixes

Three separate places used to branch on the value type: `_coerce` (read),
`_normalize_for_db` (write), and `validate_spec_value` (write validation). Each
knew part of what a type meant, so adding one meant editing three and hoping.

A `ValueTypeSpec` owns BOTH directions for one type. `from_storage` and
`to_storage` are a matched pair belonging to a single object, and nothing else
is allowed to know how a type is encoded — so a new type is one declaration, and
a round-trip property test over the registry covers every type at once.

## Read is tolerant, write is strict

`from_storage` returns `None` for anything it cannot read, because the read path
degrades an unreadable stored value to the spec default rather than failing a
request — a value written by an older version, or corrupted, must not take down
every page that touches settings. `to_storage` raises `ValueError` instead,
because the write path is where a caller can still be told it is wrong.

Which types are real is a declaration, validated by
`SettingValueTypeRegistry` — the sixth registry of this shape, after
permissions, capabilities, audit actions, feature flags and setting domains.
"""

from __future__ import annotations

import json as _json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from dotmac_kernel.money import Money, MoneyError, currency

if TYPE_CHECKING:  # avoids a runtime cycle: `features` imports this module
    from dotmac_kernel.modules import AnyManifest

# Which column a type's storage form lands in. `text` uses `value_text`, `json`
# uses `value_json` — the pair the `ck_domain_settings_value_alignment` CHECK
# constraint governs.
Storage = Literal["text", "json"]


class SettingValueType(str):
    """A value type — an open, registered string.

    A `str` subclass for the same reasons as `SettingDomain`: existing call
    sites read `SettingValueType.integer` and `.value`, and a type compares
    equal to its plain-string form in a query. Instances are NOT singletons —
    compare with `==`, never `is`.
    """

    __slots__ = ()

    string: SettingValueType
    integer: SettingValueType
    boolean: SettingValueType
    json: SettingValueType
    money: SettingValueType

    @property
    def value(self) -> str:
        return str(self)

    def __repr__(self) -> str:
        return f"SettingValueType({str(self)!r})"


class ValueTypeError(Exception):
    """Base for value-type declaration and validation failures."""


class DuplicateValueTypeError(ValueTypeError):
    """Two modules declared the same value type — there is no single owner."""


class UndeclaredValueTypeError(ValueTypeError, ValueError):
    """A value type was used that no installed module declares.

    Subclasses `ValueError` so a caller already catching that for a bad value
    keeps catching this, rather than the exception escaping as a 500.
    """


@dataclass(frozen=True, slots=True)
class ValueTypeSpec:
    """One value type, and the only thing that knows how it is encoded.

    `from_storage` and `to_storage` are a matched pair; a type whose halves
    disagree is caught by the round-trip test rather than in production.
    """

    code: SettingValueType
    storage: Storage
    # Stored form -> Python value. `None` means "cannot read this", which the
    # resolver degrades to the spec default.
    from_storage: Callable[[object], object | None]
    # Python value -> stored form. Raises `ValueError` on anything invalid.
    to_storage: Callable[[object], object]
    description: str = ""


# ── The kernel's built-in types ─────────────────────────────────────────────


def _string_from(raw: object) -> object | None:
    return raw if isinstance(raw, str) else str(raw)


def _string_to(value: object) -> object:
    if value is None:
        raise ValueError("a string setting may not be null")
    return str(value)


def _boolean_from(raw: object) -> object | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalised = raw.strip().lower()
        if normalised in {"1", "true", "yes", "on"}:
            return True
        if normalised in {"0", "false", "no", "off"}:
            return False
    return None


def _boolean_to(value: object) -> object:
    # `bool(value)` would silently accept "no" as True, which is how a config
    # meant to disable something ends up enabling it.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and _boolean_from(value) is not None:
        return "true" if _boolean_from(value) else "false"
    raise ValueError(f"not a boolean: {value!r}")


def _integer_from(raw: object) -> object | None:
    if isinstance(raw, bool):  # bool is an int subclass; not an integer setting
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def _integer_to(value: object) -> object:
    coerced = _integer_from(value)
    if coerced is None:
        raise ValueError(f"not an integer: {value!r}")
    return str(coerced)


def _json_from(raw: object) -> object | None:
    # Already a Python object when it comes from the JSON column.
    return raw


def _json_to(value: object) -> object:
    if not isinstance(value, dict):
        raise ValueError(f"a json setting must be an object, got {type(value)!r}")
    return value


def _money_from(raw: object) -> object | None:
    """`{"amount": "12.34", "currency": "NGN"}` -> `Money`.

    The amount is stored as a STRING, not a number: JSON numbers are IEEE
    doubles in most parsers, and the whole point of `Money` is that the amount
    is exact.
    """
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, Mapping):
        return None
    amount, code = raw.get("amount"), raw.get("currency")
    if not isinstance(amount, str) or not isinstance(code, str):
        return None
    try:
        return Money.of(Decimal(amount), currency(code))
    except (MoneyError, InvalidOperation, ValueError):
        return None


def _money_to(value: object) -> object:
    if not isinstance(value, Money):
        raise ValueError(
            f"a money setting must be a dotmac_kernel.money.Money, got "
            f"{type(value)!r} — constructing one names the currency, which a "
            "bare number cannot"
        )
    return {"amount": str(value.amount), "currency": value.currency.code}


KERNEL_VALUE_TYPES: tuple[ValueTypeSpec, ...] = (
    ValueTypeSpec(
        code=SettingValueType("string"),
        storage="text",
        from_storage=_string_from,
        to_storage=_string_to,
        description="Free text.",
    ),
    ValueTypeSpec(
        code=SettingValueType("integer"),
        storage="text",
        from_storage=_integer_from,
        to_storage=_integer_to,
        description="A whole number. `bool` is rejected despite subclassing int.",
    ),
    ValueTypeSpec(
        code=SettingValueType("boolean"),
        storage="text",
        from_storage=_boolean_from,
        to_storage=_boolean_to,
        description="True/false, accepting the usual on/off spellings on read.",
    ),
    ValueTypeSpec(
        code=SettingValueType("json"),
        storage="json",
        from_storage=_json_from,
        to_storage=_json_to,
        description="An arbitrary JSON object.",
    ),
    ValueTypeSpec(
        code=SettingValueType("money"),
        storage="json",
        from_storage=_money_from,
        to_storage=_money_to,
        description=(
            "An exact amount with its currency, stored as "
            '{"amount": "<decimal string>", "currency": "<ISO-4217>"}.'
        ),
    ),
)

for _spec in KERNEL_VALUE_TYPES:
    setattr(SettingValueType, str(_spec.code), _spec.code)
del _spec


# ── The registry ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SettingValueTypeRegistry:
    """The declared value types, by code. Construction IS validation."""

    spec_by_code: Mapping[str, ValueTypeSpec]

    def __post_init__(self) -> None:
        # A frozen dataclass holding a `dict` is not immutable — the field
        # cannot be rebound but the mapping can still be mutated, which does not
        # satisfy the typed-contract standard. Wrap a private copy.
        object.__setattr__(
            self, "spec_by_code", MappingProxyType(dict(self.spec_by_code))
        )

    @classmethod
    def from_specs(cls, specs: Iterable[ValueTypeSpec]) -> SettingValueTypeRegistry:
        by_code: dict[str, ValueTypeSpec] = {}
        for spec in specs:
            existing = by_code.get(str(spec.code))
            if existing is not None and existing is not spec:
                raise DuplicateValueTypeError(
                    f"value type {str(spec.code)!r} declared twice — a value "
                    "type has one owning declaration, because it owns how its "
                    "values are encoded"
                )
            by_code[str(spec.code)] = spec
        return cls(by_code)

    @classmethod
    def from_manifests(
        cls, manifests: Iterable[AnyManifest]
    ) -> SettingValueTypeRegistry:
        """The kernel's built-ins plus whatever modules declare."""
        return cls.from_specs(
            (
                *KERNEL_VALUE_TYPES,
                *(
                    spec
                    for manifest in manifests
                    for spec in manifest.setting_value_types
                ),
            )
        )

    def is_declared(self, code: SettingValueType | str) -> bool:
        return str(code) in self.spec_by_code

    def require(self, code: SettingValueType | str) -> ValueTypeSpec:
        try:
            return self.spec_by_code[str(code)]
        except KeyError:
            raise UndeclaredValueTypeError(
                f"setting value type {str(code)!r} is not declared by any "
                "installed module — declare a `ValueTypeSpec` on the owning "
                "module's manifest (`setting_value_types=(...)`)"
            ) from None

    def codes(self) -> frozenset[SettingValueType]:
        return frozenset(SettingValueType(code) for code in self.spec_by_code)


_active: SettingValueTypeRegistry = SettingValueTypeRegistry.from_specs(
    KERNEL_VALUE_TYPES
)


def install_setting_value_types(registry: SettingValueTypeRegistry) -> None:
    """Install the process-active registry (called by `create_app`)."""
    global _active
    _active = registry


def active_setting_value_types() -> SettingValueTypeRegistry:
    """The process-active registry.

    Defaults to the kernel's built-ins rather than to empty, unlike the
    declaration registries that gate authorization or writes. An empty default
    here would make every settings read fail in any process that has not built
    an app — a worker, a CLI, a migration helper — while adding no safety: an
    undeclared value type is a code defect, not an untrusted input.
    """
    return _active


__all__ = [
    "KERNEL_VALUE_TYPES",
    "DuplicateValueTypeError",
    "SettingValueType",
    "SettingValueTypeRegistry",
    "Storage",
    "UndeclaredValueTypeError",
    "ValueTypeError",
    "ValueTypeSpec",
    "active_setting_value_types",
    "install_setting_value_types",
]
