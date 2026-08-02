"""Unit tests for revocation-list import (product half of the WS8 slice).

Two properties carry the security value here:

1. **Import takes effect immediately.** Grants already held from a revoked
   lineage are revoked in the same transaction. Checking revocation only at the
   next licence application would leave a revoked customer fully entitled until
   they happen to receive another licence — for a perpetual licence, forever.
2. **Revoked ids are permanently cumulative.** The kernel enforces monotonic
   `list_version`; it cannot know that a well-ordered newer list quietly
   dropped an id. That check lives here, and refuses the import.

Recovery is a NEW lineage, never removal from the list — proven at the bottom.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from dotmac_kernel import CapabilityCatalogue, FeatureManifest, is_entitled
from dotmac_kernel.models import Tenant
from dotmac_kernel.testing import FakeLicenceSigner
from sqlalchemy.orm import Session

from app.features.licensing.config import ReceiverConfig
from app.features.licensing.models import TenantRevocationList
from app.features.licensing.revocation import (
    RevocationImportRegressionError,
    import_revocation_list,
    stored_revoked_ids,
)
from app.features.licensing.service import apply_licence

NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)

_CATALOGUE = CapabilityCatalogue.from_manifests(
    [FeatureManifest(name="billing", capabilities=("billing.use", "billing.export"))]
)


@pytest.fixture
def signer() -> FakeLicenceSigner:
    return FakeLicenceSigner(key_id="vendor-key-1")


def _config(signer: FakeLicenceSigner) -> ReceiverConfig:
    return ReceiverConfig(
        keyring=signer.keyring(), deployment_id=None, require_binding=False
    )


def _apply(db, tenant, signer, envelope):
    return apply_licence(
        db,
        tenant_id=tenant.id,
        envelope=envelope,
        config=_config(signer),
        catalogue=_CATALOGUE,
        now=NOW,
    )


def _import(db, tenant, signer, *, version: int, ids: list[str]):
    envelope = signer.sign_revocation_list(
        list_version=version, revoked_licence_ids=ids
    )
    return import_revocation_list(
        db, tenant_id=tenant.id, envelope=envelope, config=_config(signer)
    )


# ── Import applies immediately ──────────────────────────────────────────────


def test_import_revokes_already_applied_grants_immediately(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    """THE point of the slice: the customer is entitled, the list arrives, and
    access is gone before any further licence traffic."""
    _apply(
        db,
        tenant_row,
        signer,
        signer.envelope(
            licence_id="lic-1",
            licence_version=1,
            capabilities=[{"code": "billing.use"}, {"code": "billing.export"}],
        ),
    )
    assert is_entitled(
        db, tenant_id=tenant_row.id, capability_code="billing.use"
    ).allowed

    result = _import(db, tenant_row, signer, version=1, ids=["lic-1"])

    assert result.accepted is True
    assert result.revoked_codes == ("billing.export", "billing.use")
    for code in ("billing.use", "billing.export"):
        decision = is_entitled(db, tenant_id=tenant_row.id, capability_code=code)
        assert not decision.allowed
        assert decision.reason == "revoked"


def test_import_leaves_other_lineages_untouched(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    _apply(
        db,
        tenant_row,
        signer,
        signer.envelope(
            licence_id="lic-1",
            licence_version=1,
            capabilities=[{"code": "billing.use"}],
        ),
    )
    _apply(
        db,
        tenant_row,
        signer,
        signer.envelope(
            licence_id="lic-2",
            licence_version=1,
            capabilities=[{"code": "billing.export"}],
        ),
    )
    result = _import(db, tenant_row, signer, version=1, ids=["lic-1"])

    assert result.revoked_codes == ("billing.use",)
    assert is_entitled(
        db, tenant_id=tenant_row.id, capability_code="billing.export"
    ).allowed


def test_importing_an_empty_list_is_accepted_and_changes_nothing(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    """A signed "nothing is revoked" is meaningful — it is how a deployment
    learns the vendor is still speaking to it."""
    _apply(
        db,
        tenant_row,
        signer,
        signer.envelope(
            licence_id="lic-1",
            licence_version=1,
            capabilities=[{"code": "billing.use"}],
        ),
    )
    result = _import(db, tenant_row, signer, version=1, ids=[])
    assert result.accepted is True
    assert result.revoked_codes == ()
    assert is_entitled(
        db, tenant_id=tenant_row.id, capability_code="billing.use"
    ).allowed


# ── Stored state + ordering ─────────────────────────────────────────────────


def test_stored_version_and_set_persist(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    _import(db, tenant_row, signer, version=3, ids=["lic-1", "lic-2"])
    row = db.execute(sa.select(TenantRevocationList)).scalar_one()
    assert row.list_version == 3
    assert sorted(row.revoked_licence_ids) == ["lic-1", "lic-2"]
    assert stored_revoked_ids(db, tenant_id=tenant_row.id) == frozenset(
        {"lic-1", "lic-2"}
    )


def test_stale_list_is_rejected_with_the_kernel_reason(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    _import(db, tenant_row, signer, version=5, ids=["lic-1"])
    result = _import(db, tenant_row, signer, version=4, ids=["lic-1", "lic-2"])

    assert result.accepted is False
    assert result.reason == "StaleRevocationListError"
    # Stored state untouched.
    assert stored_revoked_ids(db, tenant_id=tenant_row.id) == frozenset({"lic-1"})


def test_reimporting_the_same_version_is_idempotent(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    _import(db, tenant_row, signer, version=2, ids=["lic-1"])
    result = _import(db, tenant_row, signer, version=2, ids=["lic-1"])
    assert result.accepted is True
    assert (
        db.execute(
            sa.select(sa.func.count()).select_from(TenantRevocationList)
        ).scalar_one()
        == 1
    )


def test_unsigned_or_untrusted_list_is_rejected(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    stranger = FakeLicenceSigner(key_id="stranger")
    envelope = stranger.sign_revocation_list(list_version=1, revoked_licence_ids=["x"])
    result = import_revocation_list(
        db, tenant_id=tenant_row.id, envelope=envelope, config=_config(signer)
    )
    assert result.accepted is False
    assert result.reason == "UnknownKeyError"
    assert stored_revoked_ids(db, tenant_id=tenant_row.id) == frozenset()


# ── THE cumulative canary (product side) ────────────────────────────────────


def test_a_newer_list_that_omits_a_revoked_id_is_refused(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    """Monotonic ordering is satisfied here — v2 > v1 — so only the superset
    check catches it. Without this, a compromised or buggy issuer could
    un-revoke by omission while every ordering rule still passed."""
    _apply(
        db,
        tenant_row,
        signer,
        signer.envelope(
            licence_id="lic-1",
            licence_version=1,
            capabilities=[{"code": "billing.use"}],
        ),
    )
    _import(db, tenant_row, signer, version=1, ids=["lic-1"])

    with pytest.raises(RevocationImportRegressionError, match="omits"):
        _import(db, tenant_row, signer, version=2, ids=["lic-2"])

    # Still revoked, stored state unchanged.
    assert stored_revoked_ids(db, tenant_id=tenant_row.id) == frozenset({"lic-1"})
    assert not is_entitled(
        db, tenant_id=tenant_row.id, capability_code="billing.use"
    ).allowed


def test_a_growing_list_is_accepted(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    _import(db, tenant_row, signer, version=1, ids=["lic-1"])
    result = _import(db, tenant_row, signer, version=2, ids=["lic-1", "lic-2"])
    assert result.accepted is True
    assert stored_revoked_ids(db, tenant_id=tenant_row.id) == frozenset(
        {"lic-1", "lic-2"}
    )


# ── Revoked lineages stay unusable; recovery is a new lineage ───────────────


def test_a_revoked_lineage_cannot_be_applied_again_at_any_version(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    _import(db, tenant_row, signer, version=1, ids=["lic-1"])

    result = _apply(
        db,
        tenant_row,
        signer,
        signer.envelope(
            licence_id="lic-1",
            licence_version=9,  # a much newer version of the SAME lineage
            capabilities=[{"code": "billing.use"}],
        ),
    )
    assert result.applied is False
    assert result.acknowledgement.reason == "RevokedLicenceError"
    assert not is_entitled(
        db, tenant_id=tenant_row.id, capability_code="billing.use"
    ).allowed


def test_recovery_is_a_new_lineage(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    """The vendor re-issues under a new lineage (generation); it is simply
    absent from the list, so it applies normally."""
    _import(db, tenant_row, signer, version=1, ids=["lic-1"])
    result = _apply(
        db,
        tenant_row,
        signer,
        signer.envelope(
            licence_id="lic-1-gen2",
            licence_version=1,
            capabilities=[{"code": "billing.use"}],
        ),
    )
    assert result.applied is True
    assert is_entitled(
        db, tenant_id=tenant_row.id, capability_code="billing.use"
    ).allowed


def test_revocation_state_is_per_tenant(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    """Sanity on SQLite (RLS is proven on Postgres): the stored set is keyed by
    tenant, so one tenant's import cannot revoke another's grants."""
    other = Tenant(slug="other", name="Other")
    db.add(other)
    db.flush()
    _import(db, tenant_row, signer, version=1, ids=["lic-1"])
    assert stored_revoked_ids(db, tenant_id=other.id) == frozenset()


def test_import_result_is_json_serialisable(
    db: Session, tenant_row: Tenant, signer: FakeLicenceSigner
) -> None:
    """The result is audited verbatim; a non-serialisable field would fail the
    audit write rather than the import."""
    result = _import(db, tenant_row, signer, version=1, ids=["lic-1"])
    json.dumps(
        {
            "accepted": result.accepted,
            "list_version": result.list_version,
            "revoked_licence_ids": list(result.revoked_licence_ids),
            "revoked_codes": list(result.revoked_codes),
        }
    )
