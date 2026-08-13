"""DEPRECATED — the SQL query helpers moved to :mod:`dotmac_kernel.listing`.

This module is a **re-export shim, not a second implementation**. It exists for
consumers pinned to a kernel release that published these names here; there is
no behaviour in this file and nothing in this repository imports it.

## Why they moved

Pagination, ordering and LIKE-escaping are the SQL half of one concern whose
other half — what the caller actually asked for — arrived with the
``ListDefinition``/``ListQuery``/``PageMeta`` port from ``dotmac_sub``. Keeping
``apply_pagination`` in one module and ``ListQuery.offset`` in another would
leave the list surface with two owners, which is the thing the Dotmac
source-of-truth standard exists to prevent. ``dotmac_kernel.listing`` is that
one owner.

## Retirement

The old import path is kept until every released consumer has moved. Retiring it
is a deliberate step, not a cleanup someone does in passing:

1. no in-repo importer (**already true** — this shim has zero callers here);
2. no external consumer of a released kernel still importing it;
3. then delete this module, remove it from ``SUPPORTED_MODULES``, and record the
   removal in the kernel CHANGELOG as a breaking change.

Import from ``dotmac_kernel.listing`` in new code.
"""

from __future__ import annotations

from dotmac_kernel.listing import apply_ordering, apply_pagination, escape_like

__all__ = [
    "apply_ordering",
    "apply_pagination",
    "escape_like",
]
