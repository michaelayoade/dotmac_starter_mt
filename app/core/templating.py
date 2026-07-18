"""Central Jinja2 templating: singleton environment + render() helper.

Every web route renders HTML through `render()` in this module — never
construct a separate `Jinja2Templates` instance elsewhere, so template
context always carries the same globals (`request`, `brand`,
`static_asset_url`, `current_year`) from one place.

Ported from ST:app/templates.py (`_asset_version`/`_static_asset_url`);
the sanitize/format/timeago Jinja filters from that donor are NOT ported
here. Two filters ARE registered below, `local_datetime`/`local_date`
(Task 2): the display-settings consumption point — every template renders
a `*_at` timestamp through one of these, never a raw attribute (governance:
`tests/architecture/test_web_conventions.py
::test_timestamp_renders_go_through_local_filters`). Any other filter stays
unported until a template actually calls it (avoids shipping untested
surface area).

`brand` (below) is the deployment-STATIC half of branding
(`app.core.branding.get_brand()` — defaults < brand.json < env, cached for
the process lifetime; see that module's docstring). It is installed once as
a template global, so every template can read `brand.name` etc. without a
route passing it explicitly.

The per-TENANT DB override (Task 4 / F4 fix) is resolved ONCE per request by
`app.core.branding.get_request_branding` and cached on
`request.state.branding` (see that module's docstring for the wiring/seam
decision — `require_web_auth` + the two login-route call sites are the only
places that populate it; routes never call it themselves and never change).
`render()` below is the ONE place that reads it: if `request.state.branding`
is set, it's injected into the context as `brand` UNLESS the caller's own
`context` already defines a `brand` key (a route that wants to override —
e.g. the branding editor's own live-preview render — wins; `dict.setdefault`
below encodes that precedence). If `request.state.branding` was never set
(no tenant on the request — a platform host, an error page rendered before
any branding-populating dependency ran), `context` gets no `brand` key at
all and Jinja falls through to the process-static `brand` GLOBAL installed
below — so a request with nothing tenant-specific to show still renders the
static brand, never an `UndefinedError`. Net precedence, highest first:
explicit route context > per-request tenant override > static global.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from app.core.branding import get_brand
from app.core.display import DisplaySettings, default_display
from app.core.features import FeatureManifest, NavItem

templates = Jinja2Templates(directory="templates")


def _context_display(context: Any) -> DisplaySettings:
    request = context.get("request")
    display = getattr(request.state, "display", None) if request is not None else None
    # Fail-safe for renders that never warmed request.state.display (error
    # pages, unauthenticated pages): spec defaults, never an exception.
    return display if display is not None else default_display()


@pass_context
def local_datetime(context: Any, value: datetime | None) -> str:
    if value is None:
        return ""
    display = _context_display(context)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)  # SQLite returns naive UTC
    return value.astimezone(display.timezone).strftime(display.datetime_format)


@pass_context
def local_date(context: Any, value: datetime | date | None) -> str:
    if value is None:
        return ""
    display = _context_display(context)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(display.timezone).date()
    return value.strftime(display.date_format)


templates.env.filters["local_datetime"] = local_datetime
templates.env.filters["local_date"] = local_date


def install_surface_globals(
    manifests: Sequence[FeatureManifest], disabled: set[str], web_enabled: bool
) -> None:
    """Set the process-static `enabled_features`/`nav_items` Jinja globals
    from the loaded manifests — the ONE place templates learn which
    features are on and what the sidebar contains (F1/F5 capability model;
    see `app.core.features`'s module docstring). Called once from
    `app.main` (module import time, right after `load_manifests`) — config
    is process-static, so these globals are correct for the process
    lifetime; a config change requires a restart, same as every other
    process-static setting in this app.

    `enabled_features` always reflects the real enabled-feature set
    (`DISABLED_FEATURES` + each manifest's `enabled_by_default`), regardless
    of `web_enabled` — it's the general "is this feature on" flag templates
    use for optional-slot conditionals (e.g.
    `templates/admin/parties/detail.html`'s `{% if 'custom_fields' in
    enabled_features %}`), not specifically a web-surface concept.
    `nav_items` is `()` when `web_enabled` is False — there is no sidebar to
    populate when the whole `/admin` HTML surface is off.
    """
    enabled = frozenset(
        manifest.name
        for manifest in manifests
        if manifest.name not in disabled and manifest.enabled_by_default
    )
    nav_items: tuple[NavItem, ...] = ()
    if web_enabled:
        collected: list[NavItem] = []
        for manifest in manifests:
            if manifest.name not in enabled:
                continue
            collected.extend(
                replace(item, feature=manifest.name) for item in manifest.nav
            )
        nav_items = tuple(collected)
    templates.env.globals["enabled_features"] = enabled
    templates.env.globals["nav_items"] = nav_items


# Safe defaults so any template render before `install_surface_globals` has
# run (e.g. a test that builds its own throwaway app and never calls it)
# degrades to "no optional features, no nav" rather than a Jinja
# UndefinedError — `app.main` overwrites these at import time with the real,
# manifest-derived values.
templates.env.globals.setdefault("enabled_features", frozenset())
templates.env.globals.setdefault("nav_items", ())


@lru_cache(maxsize=256)
def _asset_version(path: str) -> str:
    """sha256 of the asset's content, truncated — a cache-busting token.

    Ported from ST:app/templates.py::_asset_version. A missing/unbuilt
    asset (e.g. `static/css/main.css` before `npm run css:build` has ever
    run) degrades to "missing" instead of raising, so a template render
    never 500s over an absent static file.
    """
    normalized = path.split("?", 1)[0].lstrip("/")
    if not normalized.startswith("static/"):
        return "missing"
    try:
        return sha256(Path(normalized).read_bytes()).hexdigest()[:12]
    except OSError:
        return "missing"


def static_asset_url(path: str) -> str:
    """Build a `/static/...` URL with a `?v=<hash>` cache-busting param."""
    normalized = "/" + path.lstrip("/")
    separator = "&" if "?" in normalized else "?"
    return f"{normalized}{separator}v={_asset_version(normalized)}"


def current_year() -> int:
    return datetime.now(UTC).year


# Deployment-static brand identity — see this module's docstring for the
# static/tenant-override split. `get_brand()` is `lru_cache`d, so this is a
# cheap dict lookup after the first call, not a re-read of brand.json.
templates.env.globals["brand"] = get_brand()
templates.env.globals["static_asset_url"] = static_asset_url
templates.env.globals["current_year"] = current_year


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    """Render template `name` with `context`, returning an HTMLResponse.

    `request` is threaded into the context automatically (Jinja2Templates'
    new-style call convention requires it as the first positional arg, and
    templates reference it directly — e.g. `request.url.path`). `status_code`
    defaults to 200; branded HTML error pages (app.core.errors._negotiate)
    pass the envelope's real status (404, 500, ...) so the HTTP status line
    matches the JSON sibling response, not just the rendered body.

    Per-request tenant branding enrichment (Task 4 / F4 fix): `context`
    gets a `brand` key from `request.state.branding` when one is present and
    `context` didn't already define its own — see this module's docstring
    for the full precedence rule and why no route needs to change to pick
    this up.
    """
    ctx = dict(context or {})
    branding = getattr(request.state, "branding", None)
    if branding is not None:
        ctx.setdefault("brand", branding)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
