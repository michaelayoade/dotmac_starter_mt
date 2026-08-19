"""Internal transaction mechanics shared by kernel services.

This module deliberately has no engine, settings or session-factory imports.
Kernel services that receive an assembly-owned ``Session`` may therefore use a
SAVEPOINT without importing :mod:`dotmac_kernel.db` and constructing the
kernel's configured database runtime. ``dotmac_kernel.db`` remains the one
public transaction authority and re-exports ``conflict_savepoint``.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy.orm import Session


@contextmanager
def conflict_savepoint(db: Session) -> Generator[None, None, None]:
    """Run an expected-conflict mutation inside a SAVEPOINT (F3 fix).

    The caller's session boundary owns the outer transaction and may establish
    transaction-local state such as ``app.current_tenant`` for RLS. A bare
    ``db.rollback()`` would discard that entire transaction and its local
    state. ``db.begin_nested()`` instead rolls back only the SAVEPOINT while
    preserving the caller's transaction, then re-raises the exception
    unchanged.

    IMPORTANT: the mutation (``db.add(...)``, or setting attributes on an
    already-loaded object) must happen INSIDE the ``with`` block. Entering a
    nested transaction auto-flushes pending changes before the SAVEPOINT is
    established, so a mutation made before this helper would not be protected.

    Expected usage::

        try:
            with conflict_savepoint(db):
                db.add(row)
                db.flush()
        except IntegrityError as exc:
            raise ConflictError("...") from exc

    A service must never call ``db.rollback()`` directly. The assembly-owned
    boundary remains responsible for committing or rolling back the outer
    transaction.
    """
    with db.begin_nested():
        yield


__all__ = ["conflict_savepoint"]
