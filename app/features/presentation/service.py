"""Project resolved kernel brand data into ``dotmac-ui`` token CSS.

This is the assembly adapter from ADR-0006's composition diagram. It is the
only layer that imports both authorities: ``dotmac_kernel.branding`` resolves
runtime data and ``dotmac_ui`` owns how allowed colours become presentation.

Template Studio is deliberately absent. Notification authoring may consume a
resolved brand in a future rendering context, but it does not own brand
precedence, token generation, stylesheet delivery, or theme selection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from dotmac_kernel.branding import load_branding
from dotmac_kernel.models import Tenant
from dotmac_ui import BrandOverride, render_brand_css
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_PACKAGE_DEFAULTS_CSS = (
    "/* Runtime brand projection unavailable; dotmac-ui package defaults apply. */\n"
)


@dataclass(frozen=True, slots=True)
class BrandStylesheet:
    """CSS response body plus non-sensitive operational attribution."""

    css: str
    source: Literal["generated", "package-defaults"]


def project_brand_stylesheet(db: Session, tenant: Tenant | None) -> BrandStylesheet:
    """Generate the tenant's token overrides, failing safely to UI defaults.

    The fallback is an intentionally empty stylesheet rather than a second
    palette: the already-loaded ``dotmac-ui`` CSS remains the one generic
    default authority. No complete brand payload or input value is logged.
    """
    if tenant is None:
        return BrandStylesheet(_PACKAGE_DEFAULTS_CSS, "package-defaults")

    try:
        resolved = load_branding(db, tenant.id)
        primary = resolved.get("primary_color")
        accent = resolved.get("accent_color")
        if not isinstance(primary, str):
            raise ValueError("resolved primary colour is not text")
        if accent is not None and not isinstance(accent, str):
            raise ValueError("resolved accent colour is not text")

        generated = render_brand_css(BrandOverride(primary=primary, accent=accent))
        if generated.warnings:
            logger.error(
                "Brand projection failed contrast contract; using package defaults "
                "warning_count=%d",
                len(generated.warnings),
            )
            return BrandStylesheet(_PACKAGE_DEFAULTS_CSS, "package-defaults")
        return BrandStylesheet(generated.css, "generated")
    except Exception as exc:
        # The route is a presentation enhancement. A resolver/generator failure
        # must not take down login or the admin portal, and logging the exception
        # text could disclose the rejected brand input. Request-id correlation
        # remains available through ObservabilityMiddleware.
        logger.error(
            "Brand projection failed; using package defaults error_type=%s",
            type(exc).__name__,
        )
        return BrandStylesheet(_PACKAGE_DEFAULTS_CSS, "package-defaults")


__all__ = ["BrandStylesheet", "project_brand_stylesheet"]
