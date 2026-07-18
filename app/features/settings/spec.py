"""Initial setting spec declarations for this app.

Importing this module registers the specs with the core registry
(`app.core.settings_resolver.register_specs`) as a side effect —
`app/features/settings/__init__.py` imports this module, so anything that
loads the `settings` feature package (e.g. `app.core.features.load_manifests`
importing `app.features.settings.feature`, which first imports the parent
package) registers these as a byproduct.

`custom_fields/max_per_entity` is ported from ERP's orphan spec (declared but
never consumed there); it's actually consumed by this app's `custom_fields`
feature in a later task.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.settings_models import SettingDomain, SettingValueType
from app.core.settings_resolver import SettingSpec, register_specs


def _validate_timezone(value: object) -> None:
    try:
        ZoneInfo(str(value))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown IANA timezone: {value!r}") from exc


def _validate_strftime(value: object) -> None:
    fmt = str(value)
    if "%" not in fmt:
        raise ValueError("format must contain at least one strftime % directive")
    try:
        datetime(2026, 1, 31, 13, 45, tzinfo=UTC).strftime(fmt)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid strftime format: {exc}") from exc


SPECS: list[SettingSpec] = [
    SettingSpec(
        domain=SettingDomain.custom_fields,
        key="max_per_entity",
        value_type=SettingValueType.integer,
        default=20,
        min_value=1,
        max_value=100,
        label="Maximum custom fields per entity",
    ),
    SettingSpec(
        domain=SettingDomain.branding,
        key="ui_branding",
        value_type=SettingValueType.json,
        default={},
        label="UI branding overrides (logo, colors, etc.)",
    ),
    SettingSpec(
        domain=SettingDomain.audit,
        key="retention_days",
        value_type=SettingValueType.integer,
        default=365,
        min_value=1,
        label="Audit event retention period (days)",
    ),
    SettingSpec(
        domain=SettingDomain.display,
        key="timezone",
        value_type=SettingValueType.string,
        default="UTC",
        label="Display timezone (IANA name, e.g. Europe/London)",
        validator=_validate_timezone,
    ),
    SettingSpec(
        domain=SettingDomain.display,
        key="date_format",
        value_type=SettingValueType.string,
        default="%Y-%m-%d",
        label="Date display format (strftime)",
        validator=_validate_strftime,
    ),
    SettingSpec(
        domain=SettingDomain.display,
        key="datetime_format",
        value_type=SettingValueType.string,
        default="%Y-%m-%d %H:%M",
        label="Date+time display format (strftime)",
        validator=_validate_strftime,
    ),
]

register_specs(SPECS)
