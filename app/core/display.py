"""Per-request tenant display settings (timezone + date/datetime formats).

Mirrors app.core.branding: resolved at most once per request, memoized on
`request.state.display`, warmed by `require_web_auth`. Templates consume it
ONLY via the `local_datetime`/`local_date` Jinja filters registered in
app.core.templating (governance:
tests/architecture/test_web_conventions.py::test_timestamp_renders_go_through_local_filters).

The JSON API is deliberately untouched: responses remain ISO-8601 UTC.
Display formatting is a web-portal presentation concern; API consumers do
their own localization.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.settings_models import SettingDomain
from app.core.settings_resolver import get_spec, resolve_value

_UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class DisplaySettings:
    timezone: ZoneInfo
    date_format: str
    datetime_format: str


def default_display() -> DisplaySettings:
    """Spec-default display — used when there is no tenant or no warmed state."""
    return DisplaySettings(
        timezone=_UTC,
        date_format=str(get_spec(SettingDomain.display, "date_format").default),
        datetime_format=str(get_spec(SettingDomain.display, "datetime_format").default),
    )


def load_display(db: Session, tenant_id: UUID) -> DisplaySettings:
    tz_name = resolve_value(db, SettingDomain.display, "timezone", tenant_id=tenant_id)
    try:
        tz = ZoneInfo(str(tz_name))
    except (ZoneInfoNotFoundError, ValueError):
        # resolve_value already degrades validator-failing rows to the spec
        # default, so this is belt-and-braces (e.g. tzdata missing at
        # runtime): a render must never 500 over a timezone lookup.
        tz = _UTC
    return DisplaySettings(
        timezone=tz,
        date_format=str(
            resolve_value(db, SettingDomain.display, "date_format", tenant_id=tenant_id)
        ),
        datetime_format=str(
            resolve_value(
                db, SettingDomain.display, "datetime_format", tenant_id=tenant_id
            )
        ),
    )


def get_request_display(request: Request, db: Session) -> DisplaySettings:
    cached = getattr(request.state, "display", None)
    if cached is not None:
        return cached
    tenant = getattr(request.state, "tenant", None)
    display = load_display(db, tenant.id) if tenant is not None else default_display()
    request.state.display = display
    return display


__all__ = [
    "DisplaySettings",
    "default_display",
    "get_request_display",
    "load_display",
]
