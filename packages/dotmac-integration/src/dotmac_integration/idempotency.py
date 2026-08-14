"""At-most-once execution — ADAPTED from the kernel, never reimplemented.

ADR-0014 and hard rule 21 give at-most-once execution exactly ONE owner in the
fleet: `dotmac_kernel.idempotency`. This module is a control plane that runs
effects against external providers, which is precisely the situation that
tempts a second ledger — and a second ledger is the failure ADR-0014 records,
because "did this run?" then has two answers and no tiebreak.

So this file is thin on purpose. It picks the tenant-free contract
(`execute_once_platform`), because every table in `mod_intg` is platform-plane
and there is no tenant to scope by, and it fixes the scope naming so integration
records are legible beside every other at-most-once record in the ledger.

## What this is NOT

`delivery_attempts.idempotency_key` is not this. That column deduplicates
ENQUEUE — the same logical effect queued twice becomes one row. Whether the
effect RUNS at most once is the question answered here, by the kernel. Conflating
them is how a queue ends up believing it delivered something it never attempted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# NOT imported at module level, deliberately. `dotmac_kernel.idempotency` pulls
# in `dotmac_kernel.db`, which constructs an Engine from settings AT IMPORT — so
# a top-level import here would make `dotmac_integration` unimportable without a
# DATABASE_URL, and break the clean-environment import check every module
# release runs. Deferred to call time, where a Session already exists anyway.
#
# The TYPE-ONLY imports below are safe for the same reason they are useful: they
# exist for the checker and are never evaluated at runtime, so the signature can
# name the kernel's real contract without importing an Engine to do it.
if TYPE_CHECKING:
    from datetime import datetime

    from dotmac_kernel.idempotency import IdempotentOutcome, Operation
    from sqlalchemy.orm import Session

__all__ = ["INTEGRATION_SCOPE_PREFIX", "run_effect_once", "scope_for"]

#: Every scope this module reserves in the shared ledger. A prefix rather than a
#: single scope so `integration.delivery` and `integration.inbox` are
#: distinguishable in a ledger dump — and so nothing outside this module can
#: claim an `integration.*` scope by accident.
INTEGRATION_SCOPE_PREFIX = "integration"


def scope_for(mechanism: str) -> str:
    """`integration.<mechanism>`, validated.

    A mechanism with a dot in it would create a scope this module cannot later
    distinguish from a nested one, so it is refused rather than normalised.
    """
    cleaned = mechanism.strip().lower()
    if not cleaned or "." in cleaned or not cleaned.replace("_", "").isalnum():
        raise ValueError(
            f"mechanism {mechanism!r} must be a single lowercase token, e.g. "
            "'delivery' or 'inbox'"
        )
    return f"{INTEGRATION_SCOPE_PREFIX}.{cleaned}"


def run_effect_once(
    db: Session,
    *,
    mechanism: str,
    key: str,
    operation: Operation,
    operation_name: str | None = None,
    fingerprint: str | None = None,
    correlation_id: str | None = None,
    expires_at: datetime | None = None,
) -> IdempotentOutcome:
    """Run `operation` at most once per `(scope, key)`, platform-scoped.

    A direct adaptation of `dotmac_kernel.idempotency.execute_once_platform` —
    no local reservation table, no second fingerprint column, no parallel
    replay logic. If this function ever grows those, the module has acquired an
    at-most-once ledger it is not allowed to own.

    "Direct adaptation" is now literal: every parameter below is the kernel's,
    and the only thing this adds is `mechanism` → `scope`. It previously took a
    `payload` the kernel has no parameter for, and an `operation` taking no
    arguments where the kernel calls `operation(db)` — so the function raised
    `TypeError` on its first call and could never have run. It had no caller and
    no test, which is why nothing noticed; `make type-check` never covered this
    package (see the Makefile change in the same commit).

    `fingerprint` is the kernel's own column and the closest thing to what the
    old `payload` argument appeared to intend — ADR-0014 § "the fingerprint is
    its own column". It is passed through rather than reinvented.
    """
    from dotmac_kernel.idempotency import execute_once_platform

    return execute_once_platform(
        db,
        scope=scope_for(mechanism),
        key=key,
        operation=operation,
        operation_name=operation_name,
        fingerprint=fingerprint,
        correlation_id=correlation_id,
        expires_at=expires_at,
    )
