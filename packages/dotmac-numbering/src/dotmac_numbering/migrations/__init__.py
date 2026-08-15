"""Public locator for Numbering's installed Alembic lineage.

A consuming assembly composes this directory into Alembic's
``version_locations``. Resolving it from the installed package keeps consumers
independent of source checkouts and of this package's private layout.

Shipping the lineage as package data without a locator is not enough: the
Starter can hard-code ``packages/dotmac-numbering/...`` because the package sits
in the same checkout, but a cross-repository consumer cannot, and reaching into
``__file__`` from outside would make this package's directory structure part of
its contract. ERP and Sub are both cross-repository consumers here, so the gap
would be paid for immediately.
"""

from __future__ import annotations

from pathlib import Path


def versions_dir() -> Path:
    """Return the installed directory containing this module's revisions."""
    return Path(__file__).resolve().parent / "versions"


__all__ = ["versions_dir"]
