"""This feature's own setting declarations.

A spec belongs with the code that READS it — CLAUDE.md's "Declare a setting
spec" extension point says so, and typed resolution makes it load-bearing
rather than a preference: `resolve(db, MAX_PER_ENTITY)` returns `int` because
the spec object carries the type, and a reader can only hold that object if the
spec lives somewhere it may import.

That is why the seven original specs could not use it. All of them were
declared in `app/features/settings/spec.py` while being read from `auth`,
`rbac`, `custom_fields`, or the kernel — and features may not import each
other, so every reader was forced onto the string-keyed `resolve_value` and got
`object` back. This module is that inversion corrected for `custom_fields`.
"""

from __future__ import annotations

from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import SettingDomain
from dotmac_kernel.settings_resolver import SettingSpec, register_specs

# Typed: `SettingSpec[int]`, so `resolve(db, MAX_PER_ENTITY, ...)` is an `int`
# at every call site and a wrong `default=` is a type error here, at the
# declaration, rather than a surprise at the reader.
MAX_PER_ENTITY: SettingSpec[int] = SettingSpec(
    domain=SettingDomain.custom_fields,
    key="max_per_entity",
    value_type=SettingValueType.integer,
    default=20,
    min_value=1,
    max_value=100,
    label="Maximum custom fields per entity",
    description=(
        "Ceiling on custom-field definitions for one entity type. Raising "
        "it widens every row's JSONB payload, so raise it deliberately."
    ),
)

register_specs([MAX_PER_ENTITY])

__all__ = ["MAX_PER_ENTITY"]
