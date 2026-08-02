"""Revocation-list import — the product half of the WS8 revocation slice.

Importing a signed revocation list must take effect **immediately**, not at the
next licence application. Checking revocation only on the next apply would
leave a revoked customer fully entitled for however long it takes them to
receive another licence — which, for a perpetual or long-dated licence, is
forever. So an accepted import revokes the local WS2 grants sourced from every
revoked lineage in the same transaction.

Two rules the kernel verifier cannot enforce for us, so they live here:

- **Cumulative sets.** Revoked ids are permanently cumulative (ruled
  2026-08-02). The kernel enforces monotonic `list_version`, but a higher
  version that silently omits an earlier id would look perfectly ordered while
  restoring access. An import whose set is not a superset of the stored one is
  refused.
- **Applied licences stay checked.** The stored set is fed back into
  `verify_licence` on every subsequent application, so a revoked lineage can
  never be re-applied — including at a higher version. Recovery is a NEW
  lineage, which is simply absent from the list.

Transaction-authority contract: receives a `Session`, only add/flush.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.entitlements import TenantEntitlementGrant
from dotmac_kernel.licensing import LicenceError, verify_revocation_list
from sqlalchemy.orm import Session

from app.features.licensing.config import ReceiverConfig, load_receiver_config
from app.features.licensing.models import TenantRevocationList

# Grants written by an applied licence carry this provenance prefix (see
# `service.apply_licence`), which is how an import finds what to revoke.
LICENCE_SOURCE_PREFIX = "licence:"


class RevocationImportRegressionError(RuntimeError):
    """The imported list omits a licence id the tenant already has revoked —
    silent un-revocation. Refused."""


@dataclass(frozen=True, slots=True)
class RevocationImportResult:
    """The explainable outcome of an import. `revoked_codes` is what actually
    changed locally, which is the operator-visible part."""

    accepted: bool
    list_version: int | None
    revoked_licence_ids: tuple[str, ...]
    revoked_codes: tuple[str, ...]
    reason: str | None = None


def stored_revoked_ids(db: Session, *, tenant_id: UUID) -> frozenset[str]:
    """The tenant's current revoked set — fed into every `verify_licence`."""
    row = db.execute(
        sa.select(TenantRevocationList).where(
            TenantRevocationList.tenant_id == tenant_id
        )
    ).scalar_one_or_none()
    if row is None:
        return frozenset()
    return frozenset(str(i) for i in row.revoked_licence_ids)


def import_revocation_list(
    db: Session,
    *,
    tenant_id: UUID,
    envelope: Mapping[str, object],
    config: ReceiverConfig | None = None,
) -> RevocationImportResult:
    """Verify and apply a signed revocation list for `tenant_id`.

    Never raises for a bad list — the rejection IS the result, with the stable
    reason code — except for the cumulative-regression case, which is a vendor
    integrity failure rather than an ordinary rejection and must be loud.
    """
    config = config or load_receiver_config()
    row = db.execute(
        sa.select(TenantRevocationList).where(
            TenantRevocationList.tenant_id == tenant_id
        )
    ).scalar_one_or_none()
    applied_version = row.list_version if row is not None else None

    try:
        verified = verify_revocation_list(
            envelope, keyring=config.keyring, applied_list_version=applied_version
        )
    except LicenceError as exc:
        return RevocationImportResult(
            accepted=False,
            list_version=applied_version,
            revoked_licence_ids=tuple(
                sorted(stored_revoked_ids(db, tenant_id=tenant_id))
            ),
            revoked_codes=(),
            reason=type(exc).__name__,
        )

    incoming = frozenset(verified.revoked_licence_ids)
    stored = stored_revoked_ids(db, tenant_id=tenant_id)
    missing = stored - incoming
    if missing:
        raise RevocationImportRegressionError(
            f"revocation list v{verified.list_version} omits licence id(s) "
            f"{sorted(missing)} already revoked for this tenant — an omission is "
            "silent un-revocation. Recovery from a mistaken revocation is a new "
            "licence lineage, never removal from the list."
        )

    # Apply immediately: every grant this tenant holds from a revoked lineage is
    # revoked now, not at the next licence application.
    revoked_codes: list[str] = []
    if incoming:
        sources = [f"{LICENCE_SOURCE_PREFIX}{i}" for i in sorted(incoming)]
        grants = (
            db.execute(
                sa.select(TenantEntitlementGrant).where(
                    TenantEntitlementGrant.tenant_id == tenant_id,
                    TenantEntitlementGrant.source.in_(sources),
                    TenantEntitlementGrant.granted.is_(True),
                )
            )
            .scalars()
            .all()
        )
        for grant in grants:
            grant.granted = False
            revoked_codes.append(grant.capability_code)
        if grants:
            db.flush()

    if row is None:
        row = TenantRevocationList(
            tenant_id=tenant_id,
            list_version=verified.list_version,
            revoked_licence_ids=sorted(incoming),
        )
        db.add(row)
    else:
        row.list_version = verified.list_version
        row.revoked_licence_ids = sorted(incoming)
    db.flush()

    return RevocationImportResult(
        accepted=True,
        list_version=verified.list_version,
        revoked_licence_ids=tuple(sorted(incoming)),
        revoked_codes=tuple(sorted(revoked_codes)),
    )


__all__ = [
    "LICENCE_SOURCE_PREFIX",
    "RevocationImportRegressionError",
    "RevocationImportResult",
    "stored_revoked_ids",
    "import_revocation_list",
]
