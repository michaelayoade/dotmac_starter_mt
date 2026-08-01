"""The reference WS8 licence receiver — verify → local WS2 grant → ack.

This is the product-data-plane side of signed licence delivery (kernel design
brief 0.1.0a7; ruled C4): the vendor control plane NEVER writes this database —
it hands over a signed envelope, and THIS service (the one owner of licence
application here) verifies it offline with `dotmac_kernel.licensing`, projects
the verified capabilities into the tenant's local WS2 grants via
`grant_entitlement` (which enforces that every code is DECLARED by an installed
module), upserts the durable `TenantAppliedLicence` replay record, and returns
the `LicenceAcknowledgement` the vendor plane tracks by version/digest.

Everything is explainable and fail-closed: a rejected application changes
NOTHING (no partial grants — codes are validated against the catalogue before
the first write) and its ack carries the stable kernel reason code.
Transaction-authority contract: receives a `Session`, only add/flush; the
request boundary owns commit.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.capabilities import CapabilityCatalogue, UndeclaredCapabilityError
from dotmac_kernel.entitlements import TenantEntitlementGrant, grant_entitlement
from dotmac_kernel.licensing import (
    AppliedLicence,
    LicenceAcknowledgement,
    LicenceError,
    payload_digest,
    verify_licence,
)
from sqlalchemy.orm import Session

from app.features.licensing.config import ReceiverConfig, load_receiver_config
from app.features.licensing.models import TenantAppliedLicence


@dataclass(frozen=True, slots=True)
class LicenceApplicationResult:
    """The explainable outcome of one application attempt. `acknowledgement`
    is what goes back to the vendor plane either way."""

    acknowledgement: LicenceAcknowledgement
    applied: bool
    granted_codes: tuple[str, ...]
    revoked_codes: tuple[str, ...]
    validity: str | None
    reapplied: bool


def _assembly_catalogue() -> CapabilityCatalogue:
    """The catalogue of capability codes DECLARED by this assembly's installed
    features — the closed world a licence may grant into. Imported lazily:
    `load_manifests` imports every feature package, including this one."""
    from dotmac_kernel.features import load_manifests

    from app.features import FEATURE_MODULES

    return CapabilityCatalogue.from_manifests(load_manifests(FEATURE_MODULES))


def _best_effort_identity(envelope: Mapping[str, object]) -> tuple[str, int, str]:
    """Identifiers for a REJECTED ack. The envelope failed verification, so
    these are unverified claims — safe only because the ack is explicitly
    `rejected` and the vendor plane flags unknown digests; a report needs a
    subject even when the document is bad."""
    licence_id, version, digest = "unknown", 0, "unknown"
    try:
        payload_b64 = envelope.get("payload_b64")
        if isinstance(payload_b64, str):
            payload = base64.urlsafe_b64decode(
                payload_b64 + "=" * (-len(payload_b64) % 4)
            )
            digest = payload_digest(payload)
            data = json.loads(payload)
            if isinstance(data, dict):
                if isinstance(data.get("licence_id"), str):
                    licence_id = data["licence_id"]
                if isinstance(data.get("licence_version"), int):
                    version = data["licence_version"]
    except (ValueError, TypeError):
        pass
    return licence_id, version, digest


def apply_licence(
    db: Session,
    *,
    tenant_id: UUID,
    envelope: Mapping[str, object],
    config: ReceiverConfig | None = None,
    catalogue: CapabilityCatalogue | None = None,
    now: datetime | None = None,
) -> LicenceApplicationResult:
    """Verify a delivered envelope and apply it to `tenant_id`, atomically with
    the caller's transaction. Never raises for a bad licence — the rejection IS
    the result (ack with the stable kernel reason code), and a rejected
    application performs no writes."""
    config = config or load_receiver_config()
    catalogue = catalogue or _assembly_catalogue()
    now = now or datetime.now(UTC)

    try:
        # First pass establishes signature + document; the replay guard needs
        # the lineage id from the document to load our applied record.
        verified = verify_licence(
            envelope,
            keyring=config.keyring,
            now=now,
            expected_deployment_id=config.deployment_id,
            require_binding=config.require_binding,
        )
        record = db.execute(
            sa.select(TenantAppliedLicence).where(
                TenantAppliedLicence.tenant_id == tenant_id,
                TenantAppliedLicence.licence_id == verified.document.licence_id,
            )
        ).scalar_one_or_none()
        if record is not None:
            verified = verify_licence(
                envelope,
                keyring=config.keyring,
                now=now,
                expected_deployment_id=config.deployment_id,
                require_binding=config.require_binding,
                applied=AppliedLicence(
                    licence_id=record.licence_id,
                    licence_version=record.licence_version,
                    digest=record.digest,
                ),
            )
        document = verified.document
        # Fail closed BEFORE the first write: every code must be declared by
        # an installed module, or nothing at all is granted.
        for grant in document.capabilities:
            catalogue.require(grant.code)
    except (LicenceError, UndeclaredCapabilityError) as exc:
        licence_id, version, digest = _best_effort_identity(envelope)
        return LicenceApplicationResult(
            acknowledgement=LicenceAcknowledgement(
                licence_id=licence_id,
                licence_version=version,
                digest=digest,
                status="rejected",
                reason=type(exc).__name__,
                deployment_id=config.deployment_id,
            ),
            applied=False,
            granted_codes=(),
            revoked_codes=(),
            validity=None,
            reapplied=False,
        )

    source = f"licence:{document.licence_id}"
    new_codes = {grant.code for grant in document.capabilities}
    for grant in document.capabilities:
        grant_entitlement(
            db,
            tenant_id=tenant_id,
            capability_code=grant.code,
            catalogue=catalogue,
            limits=dict(grant.limits),
            source=source,
        )
    # A capability this lineage granted before but no longer carries is
    # explicitly revoked (granted=False keeps provenance, WS2 style).
    stale = (
        db.execute(
            sa.select(TenantEntitlementGrant).where(
                TenantEntitlementGrant.tenant_id == tenant_id,
                TenantEntitlementGrant.source == source,
                TenantEntitlementGrant.granted.is_(True),
            )
        )
        .scalars()
        .all()
    )
    revoked_codes = tuple(
        sorted(g.capability_code for g in stale if g.capability_code not in new_codes)
    )
    for code in revoked_codes:
        grant_entitlement(
            db,
            tenant_id=tenant_id,
            capability_code=code,
            catalogue=catalogue,
            granted=False,
            source=source,
        )

    record = db.execute(
        sa.select(TenantAppliedLicence).where(
            TenantAppliedLicence.tenant_id == tenant_id,
            TenantAppliedLicence.licence_id == document.licence_id,
        )
    ).scalar_one_or_none()
    if record is None:
        record = TenantAppliedLicence(
            tenant_id=tenant_id,
            licence_id=document.licence_id,
            licence_version=document.licence_version,
            digest=verified.digest,
            validity=verified.validity,
        )
        db.add(record)
    else:
        record.licence_version = document.licence_version
        record.digest = verified.digest
        record.validity = verified.validity
    db.flush()

    return LicenceApplicationResult(
        acknowledgement=LicenceAcknowledgement(
            licence_id=document.licence_id,
            licence_version=document.licence_version,
            digest=verified.digest,
            status="applied",
            deployment_id=config.deployment_id,
        ),
        applied=True,
        granted_codes=tuple(sorted(new_codes)),
        revoked_codes=revoked_codes,
        validity=verified.validity,
        reapplied=verified.reapplied,
    )
