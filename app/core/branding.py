"""Branding resolution: deployment-static identity + per-tenant DB override.

Two layers, deliberately kept separate (see `app.core.templating`'s module
docstring for how each is wired into template context):

- `get_brand()` -- deployment-static identity (name, tagline, colors,
  support email, app URL). Resolution order (lowest to highest precedence):
  built-in defaults < `brand.json` (repo root, overridable via
  `BRAND_CONFIG_PATH`) < same-named environment variable. Cached for the
  process lifetime (`lru_cache`); a restart is required to pick up changes.
  Ported from `dotmac_sub:app/services/branding_config.py::get_brand`,
  trimmed to the keys this starter actually uses (no mobile-app payment
  scheme / from-email fields -- add them back key-by-key in `_KEY_MAP` /
  `_DEFAULTS` / `brand.json` if a downstream project needs them).

- `load_branding(db, tenant_id)` -- the static brand, with any keys present
  in the tenant's `ui_branding` domain setting
  (`app.core.settings_resolver.resolve_value(db, SettingDomain.branding,
  "ui_branding", tenant_id=...)`) overlaid on top. Per-request, not cached.
  Ported from `dotmac_starter:app/services/branding.py::get_branding`
  (the DB-override merge), adapted from that single-tenant app's
  "one row, no tenant_id" model to this app's tenant-scoped resolver.

`sanitize_branding_css` is a verbatim port of
`dotmac_starter:app/services/branding.py::sanitize_branding_css` -- an
admin-supplied `custom_css` override must not smuggle in an `@import`,
`javascript:`/`data:` URL, IE `behavior:`, CSS `expression()`, or a
`<script>` breakout via unescaped angle brackets, since the value is
rendered `| safe` into a `<style>` block.
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.settings_models import SettingDomain
from app.core.settings_resolver import resolve_value

logger = logging.getLogger(__name__)

# Friendly key -> JSON/env key. Friendly keys are what callers and templates
# use (e.g. `brand.primary_color`); the JSON keys are the flat upper-case
# names in brand.json (same convention as dotmac_sub's, so brand.json stays
# portable across the fleet's white-label deployments).
_KEY_MAP: dict[str, str] = {
    "name": "BRAND_NAME",
    "tagline": "BRAND_TAGLINE",
    "primary_color": "BRAND_PRIMARY_COLOR",
    "accent_color": "BRAND_ACCENT_COLOR",
    "support_email": "BRAND_SUPPORT_EMAIL",
    "app_url": "BRAND_APP_URL",
}

# Built-in defaults so an unconfigured deployment (no brand.json reachable)
# still renders sanely. Intentionally generic/template-neutral -- unlike
# dotmac_sub's defaults (real DotMac values), this starter ships no
# production identity to accidentally leak into a white-labeled fork.
_DEFAULTS: dict[str, str] = {
    "name": "Starter",
    "tagline": "A DotMac starter application",
    "primary_color": "#206A07",
    "accent_color": "#06B6D4",
    "support_email": "support@example.com",
    "app_url": "https://example.com",
}

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_CSS_URL = re.compile(r"""(?is)url\(\s*(["']?)(.*?)\1\s*\)""")
_URL_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
_DANGEROUS_CSS_PATTERNS = (
    re.compile(r"(?is)@import\b[^;{}]*;?"),
    re.compile(r"(?is)\bbehavior\s*:[^;{}]*;?"),
    re.compile(r"(?is)expression\s*\([^)]*\)"),
    re.compile(r"(?i)javascript\s*:"),
)


def _config_path() -> Path:
    override = os.getenv("BRAND_CONFIG_PATH")
    if override:
        return Path(override)
    # app/core/branding.py -> core -> app -> <repo root>
    return Path(__file__).resolve().parents[2] / "brand.json"


def _load_file() -> dict[str, object]:
    path = _config_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.info("brand.json not found at %s; using built-in brand defaults", path)
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read brand.json at %s: %s; using defaults", path, exc)
    return {}


@lru_cache(maxsize=1)
def get_brand() -> dict[str, str]:
    """Return the resolved static brand config as a friendly-keyed dict.

    Cached for the process lifetime -- see `reset_brand_cache()` for tests.
    """
    raw = _load_file()
    brand = dict(_DEFAULTS)
    for friendly, json_key in _KEY_MAP.items():
        # env var wins, then brand.json, then the existing default
        value = os.getenv(json_key)
        if not (isinstance(value, str) and value.strip()):
            file_value = raw.get(json_key)
            value = file_value if isinstance(file_value, str) else None
        if isinstance(value, str) and value.strip():
            brand[friendly] = value.strip()
    return brand


def reset_brand_cache() -> None:
    """Clear the cached brand config (primarily for tests)."""
    get_brand.cache_clear()


def _normalize_hex(value: object, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    candidate = value.strip()
    if not candidate.startswith("#"):
        candidate = f"#{candidate}"
    return candidate.upper() if _HEX_COLOR.match(candidate) else fallback


def _sanitize_css_url(match: re.Match[str]) -> str:
    raw_url = match.group(2).strip()
    if not raw_url:
        return ""
    scheme_match = _URL_SCHEME.match(raw_url)
    if scheme_match and scheme_match.group(1).lower() not in {"http", "https"}:
        return ""
    return match.group(0)


def sanitize_branding_css(css: Any) -> str:
    """Strip CSS constructs that could execute script or exfiltrate data.

    Verbatim port of `dotmac_starter:app/services/branding.py::sanitize_branding_css`.
    """
    if css is None:
        return ""
    sanitized = str(css).strip()
    if not sanitized or "<" in sanitized:
        return ""

    sanitized = _CSS_URL.sub(_sanitize_css_url, sanitized)
    for pattern in _DANGEROUS_CSS_PATTERNS:
        sanitized = pattern.sub("", sanitized)
    return sanitized.strip()


def load_branding(db: Session, tenant_id: UUID | None) -> dict[str, Any]:
    """Static brand, overridden per-tenant by the `ui_branding` domain setting.

    Any friendly key present in the stored override replaces the static
    value; keys the override doesn't mention keep the static brand's value.
    `primary_color`/`accent_color` overrides are validated as `#RRGGBB` hex
    (falling back to the static color on a bad value) and `custom_css` is
    run through `sanitize_branding_css` -- mirrors
    `dotmac_starter:app/services/branding.py::get_branding`'s normalization,
    since this value is admin-editable and rendered into the page.
    """
    merged: dict[str, Any] = dict(get_brand())
    override = resolve_value(
        db, SettingDomain.branding, "ui_branding", tenant_id=tenant_id, default={}
    )
    if isinstance(override, dict):
        for key, value in override.items():
            if isinstance(value, str) and value.strip():
                merged[key] = value.strip()
        if "primary_color" in override:
            merged["primary_color"] = _normalize_hex(
                override.get("primary_color"), merged["primary_color"]
            )
        if "accent_color" in override:
            merged["accent_color"] = _normalize_hex(
                override.get("accent_color"), merged["accent_color"]
            )
        if "custom_css" in override:
            merged["custom_css"] = sanitize_branding_css(override.get("custom_css"))
    return merged
