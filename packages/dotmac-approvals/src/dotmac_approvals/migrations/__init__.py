"""Public locator for Approvals' installed Alembic lineage.

A consuming assembly composes this directory into Alembic's
``version_locations``. Resolving it from this installed package keeps consumers
independent of source checkouts and of the package's private filesystem layout.

Shipping the lineage as package data without a locator is not enough: the
Starter can hard-code ``packages/dotmac-approvals/...`` because the package sits
in the same checkout, but a cross-repository consumer cannot, and reaching into
``__file__`` from outside makes this package's layout part of its contract. The
vendor control plane hit exactly that composing the module and had to write a
private shim, which is the consumer paying for a gap this package should close.
"""

from __future__ import annotations

from pathlib import Path


def versions_dir() -> Path:
    """Return the installed directory containing this module's revisions."""
    return Path(__file__).resolve().parent / "versions"


__all__ = ["versions_dir"]
