"""Unit tests for the WS8 reference receiver (`app.features.licensing`).

The end-to-end proof on the data-plane side: a vendor-signed envelope
(FakeLicenceSigner — no key custody in tests) is verified with the kernel
verifier, projected into local WS2 grants, recorded durably for replay
protection, and acknowledged by version/digest. Rejections change nothing and
carry the stable kernel reason code. (SQLite — tenancy isolation is proven by
`tests/test_licensing_receiver_isolation.py` on Postgres.)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from dotmac_kernel import CapabilityCatalogue, FeatureManifest, is_entitled
from dotmac_kernel.entitlements import TenantEntitlementGrant
from dotmac_kernel.licensing import KeyStatus, LicenceKeyRing
from dotmac_kernel.models import Tenant
from dotmac_kernel.testing import FakeLicenceSigner
from sqlalchemy.orm import Session

from app.features.licensing.config import ReceiverConfig
from app.features.licensing.models import TenantAppliedLicence
from app.features.licensing.service import apply_licence

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

_CATALOGUE = CapabilityCatalogue.from_manifests(
    [
        FeatureManifest(name="billing", capabilities=("billing.use", "billing.export")),
        FeatureManifest(name="inventory", capabilities=("inventory.use",)),
    ]
)


@pytest.fixture
def signer() -> FakeLicenceSigner:
    return FakeLicenceSigner(key_id="vendor-key-1")


def _config(signer: FakeLicenceSigner, **over) -> ReceiverConfig:
    return ReceiverConfig(
        keyring=over.get("keyring", signer.keyring()),
        deployment_id=over.get("deployment_id"),
        require_binding=over.get("require_binding", False),
    )


def _apply(db, tenant, signer, envelope, **over):
    return apply_licence(
        db,
        tenant_id=tenant.id,
        envelope=envelope,
        config=over.pop("config", _config(signer)),
        catalogue=over.pop("catalogue", _CATALOGUE),
        now=over.pop("now", NOW),
    )


def test_verify_grant_decide_ack_end_to_end(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    envelope = signer.envelope(
        licence_id="lic-1",
        licence_version=1,
        capabilities=[
            {"code": "billing.use", "limits": {"seats": 10}},
            {"code": "inventory.use"},
        ],
    )
    result = _apply(db, tenant_row, signer, envelope)

    assert result.applied is True
    assert result.granted_codes == ("billing.use", "inventory.use")
    # The explainable local decision reflects the projection (WS2).
    decision = is_entitled(db, tenant_id=tenant_row.id, capability_code="billing.use")
    assert decision.allowed and decision.limits == {"seats": 10}
    # Durable replay record.
    record = db.execute(sa.select(TenantAppliedLicence)).scalar_one()
    assert (record.licence_id, record.licence_version) == ("lic-1", 1)
    assert record.digest.startswith("sha256:")
    # The ack the vendor plane tracks.
    ack = result.acknowledgement
    assert ack.status == "applied"
    assert (ack.licence_id, ack.licence_version) == ("lic-1", 1)
    assert ack.digest == record.digest


def test_new_version_grants_and_revokes_the_delta(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    v1 = signer.envelope(
        licence_id="lic-1",
        licence_version=1,
        capabilities=[{"code": "billing.use"}, {"code": "billing.export"}],
    )
    _apply(db, tenant_row, signer, v1)
    v2 = signer.envelope(
        licence_id="lic-1",
        licence_version=2,
        capabilities=[{"code": "billing.use"}, {"code": "inventory.use"}],
    )
    result = _apply(db, tenant_row, signer, v2)

    assert result.applied is True
    assert result.revoked_codes == ("billing.export",)
    dropped = is_entitled(db, tenant_id=tenant_row.id, capability_code="billing.export")
    assert not dropped.allowed and dropped.reason == "revoked"
    assert is_entitled(
        db, tenant_id=tenant_row.id, capability_code="inventory.use"
    ).allowed
    record = db.execute(sa.select(TenantAppliedLicence)).scalar_one()
    assert record.licence_version == 2


def test_redelivery_is_idempotent(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    envelope = signer.envelope(
        licence_id="lic-1", licence_version=1, capabilities=[{"code": "billing.use"}]
    )
    _apply(db, tenant_row, signer, envelope)
    result = _apply(db, tenant_row, signer, envelope)
    assert result.applied is True
    assert result.reapplied is True
    assert (
        db.execute(
            sa.select(sa.func.count()).select_from(TenantAppliedLicence)
        ).scalar_one()
        == 1
    )


def test_stale_version_is_rejected_and_changes_nothing(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    v2 = signer.envelope(
        licence_id="lic-1", licence_version=2, capabilities=[{"code": "billing.use"}]
    )
    _apply(db, tenant_row, signer, v2)
    v1 = signer.envelope(
        licence_id="lic-1",
        licence_version=1,
        capabilities=[{"code": "billing.use"}, {"code": "billing.export"}],
    )
    result = _apply(db, tenant_row, signer, v1)

    assert result.applied is False
    assert result.acknowledgement.status == "rejected"
    assert result.acknowledgement.reason == "StaleLicenceError"
    # The rollback attempt granted nothing and the record is untouched.
    assert not is_entitled(
        db, tenant_id=tenant_row.id, capability_code="billing.export"
    ).allowed
    record = db.execute(sa.select(TenantAppliedLicence)).scalar_one()
    assert record.licence_version == 2


def test_undeclared_code_rejects_with_no_partial_grants(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    envelope = signer.envelope(
        licence_id="lic-1",
        licence_version=1,
        capabilities=[{"code": "billing.use"}, {"code": "not.a.module"}],
    )
    result = _apply(db, tenant_row, signer, envelope)

    assert result.applied is False
    assert result.acknowledgement.reason == "UndeclaredCapabilityError"
    # Fail-closed BEFORE the first write: not even the declared code landed.
    assert (
        db.execute(
            sa.select(sa.func.count()).select_from(TenantEntitlementGrant)
        ).scalar_one()
        == 0
    )
    assert (
        db.execute(
            sa.select(sa.func.count()).select_from(TenantAppliedLicence)
        ).scalar_one()
        == 0
    )


def test_bad_signature_rejects_with_kernel_reason(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    stranger = FakeLicenceSigner(key_id="vendor-key-1")  # same id, WRONG key
    envelope = stranger.envelope(licence_id="lic-1", licence_version=1)
    result = _apply(db, tenant_row, signer, envelope)
    assert result.applied is False
    assert result.acknowledgement.reason == "BadSignatureError"
    # Best-effort identity still names the lineage for the vendor's records.
    assert result.acknowledgement.licence_id == "lic-1"


def test_revoked_key_rejects(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    config = _config(
        signer, keyring=LicenceKeyRing([signer.key(status=KeyStatus.REVOKED)])
    )
    result = _apply(db, tenant_row, signer, signer.envelope(), config=config)
    assert result.applied is False
    assert result.acknowledgement.reason == "RevokedKeyError"


def test_bound_licence_requires_matching_deployment(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    envelope = signer.envelope(
        subject={"customer": "acme", "deployment_id": "dep-a"},
        capabilities=[{"code": "billing.use"}],
    )
    mismatch = _apply(
        db, tenant_row, signer, envelope, config=_config(signer, deployment_id="dep-b")
    )
    assert mismatch.applied is False
    assert mismatch.acknowledgement.reason == "DeploymentMismatchError"

    match = _apply(
        db, tenant_row, signer, envelope, config=_config(signer, deployment_id="dep-a")
    )
    assert match.applied is True
    assert match.acknowledgement.deployment_id == "dep-a"


def test_in_grace_licence_applies_with_explicit_validity(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    envelope = signer.envelope(
        capabilities=[{"code": "billing.use"}],
        expires_at="2026-07-25T00:00:00+00:00",
        grace_days=14,
    )
    result = _apply(db, tenant_row, signer, envelope)
    assert result.applied is True
    assert result.validity == "in_grace"
    assert db.execute(sa.select(TenantAppliedLicence)).scalar_one().validity == (
        "in_grace"
    )
