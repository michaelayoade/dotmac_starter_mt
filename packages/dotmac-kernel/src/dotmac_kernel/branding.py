"""Branding resolution: deployment-static identity + per-tenant DB override.

Two layers, deliberately kept separate (see `dotmac_kernel.templating`'s module
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
  (`dotmac_kernel.settings_resolver.resolve_value(db, SettingDomain.branding,
  "ui_branding", tenant_id=...)`) overlaid on top. Per-request, not cached.
  Ported from `dotmac_starter:app/services/branding.py::get_branding`
  (the DB-override merge), adapted from that single-tenant app's
  "one row, no tenant_id" model to this app's tenant-scoped resolver.

**A tenant cannot contribute CSS (2026-08-13, ADR-0006 D8).** `custom_css`
used to be accepted, sanitized by regex, and rendered `| safe` into a
`<style>` block. Both the field and `sanitize_branding_css` are gone:

- a WRITE naming a retired key is REFUSED (`reject_retired_brand_keys`, wired
  into the `ui_branding` spec validator so the form, the generic JSON settings
  editor, and the API all fail the same way);
- a legacy stored value is inert -- `custom_css` is outside
  `_KNOWN_BRAND_KEYS`, so `load_branding` never merges it and no template can
  reach it;
- the stored value is NOT erased. `retired_brand_values` reads it back for
  inventory and export, so legitimate intent can be mapped onto tokens before
  the data is deleted as a separate, deliberate act.

A denylist was the wrong shape for the problem: it had to enumerate every
dangerous CSS construct, while an attacker needed only one it had not thought
of. `load_branding`'s merge stays allowlisted to `_KNOWN_BRAND_KEYS` -- an
override key outside that set is ignored, so a stale or hand-crafted
`ui_branding` payload can never inject an arbitrary key into the template
context.

`get_request_branding(request, db)` -- Task 4 (F4 fix): resolves
`load_branding` (or the static `get_brand()` fallback, no tenant on
`request.state`) exactly ONCE per request, memoized on
`request.state.branding`. Wiring/seam decision (three shapes considered):

1. A per-router `dependencies=[Depends(web_branding)]` at every
   `include_router(..., dependencies=[...])` call site -- rejected: routers
   are feature-owned (one per feature package), so this is N call sites
   (one per feature's web router) and grows by one every time a feature
   adds a web surface -- the opposite of a single seam.
2. A route-level dependency added to every individual web route --
   rejected for the same reason, worse (one call site per ROUTE, not per
   router).
3. **Chosen**: `dotmac_kernel.web_deps.require_web_auth` (already a dependency
   of every authenticated `/admin/*` route -- one seam, zero new call
   sites) calls `get_request_branding` itself, populating
   `request.state.branding` before the route body runs. That covers every
   authenticated portal page. The two pre-auth surfaces that also render
   HTML but never go through `require_web_auth` -- `GET`/`POST
   /admin/login` (`app.features.auth.web`) -- call it explicitly (2 call
   sites, commented at each). Total: 3 call sites for the whole app,
   independent of how many features exist. `dotmac_kernel.templating.render()`
   then reads `request.state.branding` centrally (see that module's
   docstring) -- routes never pass `brand` themselves unless they want to
   override it (e.g. the branding editor's live preview).
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

from fastapi import Request
from sqlalchemy.orm import Session

from dotmac_kernel.exceptions import BadRequestError
from dotmac_kernel.settings_models import SettingDomain
from dotmac_kernel.settings_resolver import resolve_value

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

# Keys the branding editor (`app.features.settings.web._branding_form`)
# exposes and that `load_branding` is willing to merge from the tenant's
# `ui_branding` override -- enumerated from that editor's form fields. Any
# other key in the stored dict (stale shape, hand-crafted via the raw JSON
# generic editor, a future field not yet wired here) is silently ignored
# rather than merged into the render context (2b final-review follow-up,
# folded into Task 4 -- see this module's docstring).
_KNOWN_BRAND_KEYS = frozenset(
    {"name", "tagline", "logo_url", "primary_color", "accent_color"}
)

#: Brand keys that were once accepted and are now REFUSED. A stored value for
#: one of these is inert -- it is not in `_KNOWN_BRAND_KEYS`, so `load_branding`
#: never merges it and no template can reach it -- but a WRITE naming one is
#: rejected loudly rather than dropped, so an operator learns their input was
#: not applied instead of discovering it silently vanished.
#:
#: `custom_css` was tenant-supplied raw CSS rendered into a `<style>` block
#: behind a regex sanitizer. ADR-0006 D8 replaces it with an allowlisted token
#: set: a denylist of dangerous constructs is the wrong shape for CSS, because
#: it must enumerate every hazard while an attacker needs one it missed.
RETIRED_BRAND_KEYS = frozenset({"custom_css"})

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _config_path() -> Path:
    override = os.getenv("BRAND_CONFIG_PATH")
    if override:
        return Path(override)
    # `brand.json` is ASSEMBLY-owned config (the kernel provides only the
    # defaults + this loader). The kernel package must NOT resolve it relative
    # to its own installed location — a pip-installed kernel lives outside the
    # assembly. Look in the running assembly's working directory (its repo/
    # deployment root); `BRAND_CONFIG_PATH` above is the explicit override for
    # any other location.
    return Path.cwd() / "brand.json"


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


def load_branding(db: Session, tenant_id: UUID | None) -> dict[str, Any]:
    """Static brand, overridden per-tenant by the `ui_branding` domain setting.

    Any friendly key present in the stored override replaces the static
    value; keys the override doesn't mention keep the static brand's value.
    Only keys in `_KNOWN_BRAND_KEYS` are merged -- anything else in the
    stored dict is ignored (allowlist, see this module's docstring).
    `primary_color`/`accent_color` overrides are validated as `#RRGGBB` hex,
    falling back to the static color on a bad value.

    A tenant cannot contribute CSS. `RETIRED_BRAND_KEYS` is outside the
    allowlist, so a legacy `custom_css` value still sitting in a stored
    override is never merged here and no template can reach it. Reading it out
    for inventory is `retired_brand_values`, which is deliberately a separate
    call that returns data rather than a render context.
    """
    merged: dict[str, Any] = dict(get_brand())
    override = resolve_value(
        db, SettingDomain.branding, "ui_branding", tenant_id=tenant_id, default={}
    )
    if isinstance(override, dict):
        for key, value in override.items():
            if key not in _KNOWN_BRAND_KEYS:
                continue
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
    return merged


def reject_retired_brand_keys(value: Any) -> None:
    """Raise if a branding override names a key this contract has retired.

    Called by the `ui_branding` spec validator, so it fires on EVERY write path
    -- the branding form, the generic JSON settings editor, and the settings
    API alike. Putting it in the editor instead would leave the API open, which
    is the shape of gap that makes a "removed" feature reachable.

    Refusing rather than dropping is the point: a silently ignored field trains
    an operator to believe their CSS is live.
    """
    if not isinstance(value, dict):
        return
    offending = sorted(RETIRED_BRAND_KEYS.intersection(value))
    if offending:
        raise BadRequestError(
            f"Branding no longer accepts {', '.join(offending)}. Tenant-supplied "
            "CSS was removed: express brand colours through the allowlisted "
            "token fields instead. Any previously stored value is inert and is "
            "never rendered."
        )


def retired_brand_values(db: Session, tenant_id: UUID | None) -> dict[str, str]:
    """Legacy values for retired keys, for INVENTORY and export only.

    Reads the stored override directly rather than going through
    `load_branding`, precisely because `load_branding` can no longer see these
    keys. Nothing renders this: it exists so an operator can audit what a tenant
    once supplied, map any legitimate intent onto tokens, and then delete the
    data as a separate, deliberate act. Erasing it here instead would destroy
    the evidence needed to do that mapping.
    """
    override = resolve_value(
        db, SettingDomain.branding, "ui_branding", tenant_id=tenant_id, default={}
    )
    if not isinstance(override, dict):
        return {}
    return {
        key: str(override[key])
        for key in sorted(RETIRED_BRAND_KEYS)
        if key in override and str(override[key]).strip()
    }


def get_request_branding(request: Request, db: Session) -> dict[str, Any]:
    """Resolve THIS request's effective branding exactly once, memoized on
    `request.state.branding` (Task 4 / F4 fix -- see this module's docstring
    for the wiring/seam decision and the three call sites).

    Falls back to the deployment-static `get_brand()` when
    `request.state.tenant` is `None` or absent -- platform-host requests and
    any context the tenant resolver couldn't attach a tenant to. Safe to
    call more than once per request (e.g. `require_web_auth` warms the
    cache, a route calls this again for its own reasons): the second call
    returns the cached dict without a second `load_branding` DB read.
    """
    cached = getattr(request.state, "branding", None)
    if cached is not None:
        return cached
    tenant = getattr(request.state, "tenant", None)
    branding = load_branding(db, tenant.id) if tenant is not None else get_brand()
    request.state.branding = branding
    return branding


__all__ = [
    "RETIRED_BRAND_KEYS",
    "get_brand",
    "get_request_branding",
    "load_branding",
    "reject_retired_brand_keys",
    "reset_brand_cache",
    "retired_brand_values",
]
