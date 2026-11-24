"""Setting declarations this module OWNS (moved from the reference app's
`settings` feature, which this package replaces).

Domains: `branding` and `display` (kernel-named domains whose screens this
module renders), `organisation` (tenant profile) and `settings_admin` (this
module's own policy: optional maker-checker, default OFF).

Importing this module registers the specs, exactly like every other spec.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotmac_kernel.settings_models import SettingDomain, SettingValueType
from dotmac_kernel.settings_resolver import SettingSpec, register_specs

ADMIN_DOMAIN: Final = SettingDomain("settings_admin")
ORGANISATION_DOMAIN: Final = SettingDomain("organisation")

#: Domains this module declares on its manifest.
OWNED_DOMAINS: Final = (
    str(SettingDomain.branding),
    str(SettingDomain.display),
    str(ORGANISATION_DOMAIN),
    str(ADMIN_DOMAIN),
)

#: Keys that are machine state, not operator configuration. They are declared
#: (so the write path accepts them) but never offered by the generic editor,
#: history restore or export/import.
INTERNAL_KEYS: Final = frozenset({(str(ADMIN_DOMAIN), "pending_changes")})


def _validate_timezone(value: object) -> None:
    try:
        ZoneInfo(str(value))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown IANA timezone: {value!r}") from exc


_PORTABLE_DIRECTIVES = frozenset("aAbBcdfGHIjmMpSuUVwWxXyYzZ%")


def _validate_strftime(value: object) -> None:
    """Explicit tokenizer: acceptance never depends on the host libc."""
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
    try:
        datetime(2026, 1, 31, 13, 45, tzinfo=UTC).strftime(fmt)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid strftime format: {exc}") from exc


def _validate_pending(value: object) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("items", {}), dict):
        raise ValueError("pending changes must be an object with an `items` object")


def _text(key: str, label: str, description: str) -> SettingSpec[str]:
    return SettingSpec(
        domain=ORGANISATION_DOMAIN,
        key=key,
        value_type=SettingValueType.string,
        default="",
        label=label,
        description=description,
    )


SPECS: Final[list[SettingSpec[Any]]] = [
    SettingSpec(
        domain=SettingDomain.branding,
        key="ui_branding",
        value_type=SettingValueType.json,
        default={},
        label="UI branding overrides (logo, colors, etc.)",
        description=(
            "Per-tenant overrides applied over the deployment brand. Edit these "
            "on the Branding screen. Colours come from the allowlisted token "
            "fields; tenant-supplied CSS is not accepted."
        ),
    ),
    SettingSpec(
        domain=SettingDomain.display,
        key="timezone",
        value_type=SettingValueType.string,
        default="UTC",
        label="Display timezone (IANA name, e.g. Europe/London)",
        description=(
            "Timezone the admin portal renders timestamps in. The JSON API is "
            "unaffected: it always returns ISO-8601 UTC."
        ),
        validator=_validate_timezone,
    ),
    SettingSpec(
        domain=SettingDomain.display,
        key="date_format",
        value_type=SettingValueType.string,
        default="%Y-%m-%d",
        label="Date display format (strftime)",
        description="strftime format for dates without a time, e.g. %Y-%m-%d.",
        validator=_validate_strftime,
    ),
    SettingSpec(
        domain=SettingDomain.display,
        key="datetime_format",
        value_type=SettingValueType.string,
        default="%Y-%m-%d %H:%M",
        label="Date+time display format (strftime)",
        description="strftime format for dates with a time, e.g. %Y-%m-%d %H:%M.",
        validator=_validate_strftime,
    ),
    _text("legal_name", "Legal name", "The organisation's registered legal name."),
    _text("trading_name", "Trading name", "Name the organisation trades under."),
    _text("registered_address", "Registered address", "Registered office address."),
    _text("country_code", "Country code", "ISO 3166-1 alpha-2 country code."),
    _text("contact_email", "Contact e-mail", "Public contact e-mail address."),
    _text("contact_phone", "Contact phone", "Public contact phone number."),
    SettingSpec(
        domain=ADMIN_DOMAIN,
        key="maker_checker",
        value_type=SettingValueType.boolean,
        default=False,
        label="Require a second person to approve setting changes",
        description=(
            "When on, a person without the approve permission can only PROPOSE a "
            "change; a different person holding the approve permission applies "
            "it. Secret values cannot be proposed. Off by default: a single "
            "operator can change settings directly."
        ),
    ),
    SettingSpec(
        domain=ADMIN_DOMAIN,
        key="pending_changes",
        value_type=SettingValueType.json,
        default={"items": {}},
        label="Pending setting proposals (internal)",
        description="Internal state of the optional maker-checker. Not editable.",
        validator=_validate_pending,
    ),
]

register_specs(SPECS)

__all__ = ["ADMIN_DOMAIN", "INTERNAL_KEYS", "ORGANISATION_DOMAIN", "OWNED_DOMAINS"]
