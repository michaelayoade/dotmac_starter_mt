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


# Documented, Python-portable strftime directive characters (i.e. the ones
# whose behavior isn't left to the underlying platform C library — see
# https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes).
# `%` itself is the `%%` escape, handled separately below (not a "real"
# directive — it doesn't count toward the "at least one directive" rule).
_PORTABLE_DIRECTIVES = frozenset("aAbBcdfGHIjmMpSuUVwWxXyYzZ%")


def _validate_strftime(value: object) -> None:
    """Explicit tokenizer, not a substring-check + probe-render.

    `datetime.strftime` delegates any directive it doesn't recognize to the
    platform C library, which makes a naive `"%" in fmt` + try/except probe
    unreliable: on glibc, an unknown directive like `%Q` passes through as
    literal text with NO exception, `%%` (the literal-`%` escape) satisfies
    a bare substring check while containing zero real directives, and a
    dangling trailing `%` renders as a literal `%` with no exception too.
    This walks the string directive-by-directive instead, so acceptance
    never depends on what the host libc happens to do with an unknown code.
    """
    fmt = str(value)
    if "\x00" in fmt:
        raise ValueError("format string must not contain null bytes")
    real_directive_count = 0
    i = 0
    length = len(fmt)
    while i < length:
        if fmt[i] != "%":
            i += 1
            continue
        if i + 1 >= length:
            raise ValueError("dangling '%' at end of format string")
        directive = fmt[i + 1]
        if directive not in _PORTABLE_DIRECTIVES:
            raise ValueError(f"unsupported directive %{directive}")
        if directive != "%":
            real_directive_count += 1
        i += 2
    if real_directive_count == 0:
        raise ValueError("format must contain at least one strftime % directive")
    # Belt-and-braces: the tokenizer above is the real gate, but still probe
    # -render against a fixed reference datetime in case some accepted
    # directive combination is otherwise malformed.
    try:
        datetime(2026, 1, 31, 13, 45, tzinfo=UTC).strftime(fmt)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid strftime format: {exc}") from exc


SPECS: list[SettingSpec] = [
    SettingSpec(
        domain=SettingDomain.auth,
        key="registration_policy",
        value_type=SettingValueType.string,
        default="closed",
        allowed={"open", "closed"},
        label="Self-registration policy (open | closed)",
    ),
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
