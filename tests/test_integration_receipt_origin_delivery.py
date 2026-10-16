"""Hosted Postgres checks of stored ingress origin through real receipt claims."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_integration import InboundEvent, InboxReceipt, put_config_revision
from dotmac_integration.capability_registry import capability_registry
from dotmac_integration.destination_binding import (
    LocalScope,
    ProductPortDescriptorSnapshot,
    product_port_descriptor_digest,
    reconcile_product_port_descriptor,
)
from dotmac_integration.execution import receive_verified
from dotmac_integration.ingress import prepare_ingress, record_batch
from dotmac_integration.models import ConnectorConfigRevision
from dotmac_integration.receipt_delivery import (
    DeliveryError,
    ProductAcceptance,
    ProductOutcome,
    ReceiptClaims,
    build_product_request,
    product_observation_document,
    resolve_receipt_origin_evidence,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.test_integration_isolation import (  # noqa: F401
    _installed_capability_registry,
    migrated_scratch,
)
from tests.unit.test_integration_ingress import (  # noqa: F401
    IngressFake,
    _installed_integration_audit_actions,
    address,
    build,
    registry_for,
)


@pytest.mark.parametrize("wire_version", ["v1", "v2"])
@pytest.mark.parametrize("recorded_origin", [True, False])
def _reenable(db, installation, registry) -> None:
    """Re-run activation after a config rotation demotes the installation.

    put_config_revision invalidates activation by design; a rotated endpoint
    is eligible again only after the live enable path re-validates it.
    """
    from dotmac_integration import enable, set_binding_enabled
    from dotmac_integration.models import CapabilityBinding

    enable(db, installation, registry=registry)
    for binding in db.execute(
        select(CapabilityBinding).where(
            CapabilityBinding.installation_id == installation.id
        )
    ).scalars():
        set_binding_enabled(
            db, installation, binding, registry=registry, enabled=True
        )


def test_real_claim_retains_recorded_origin_across_configuration_rotation(
    migrated_scratch: tuple[str, str],  # noqa: F811
    wire_version: str,
    recorded_origin: bool,
) -> None:
    engine = create_engine(migrated_scratch[0])
    registry = registry_for(IngressFake())
    capabilities = capability_registry()
    with Session(engine) as db, db.begin():
        installation, binding, key = build(db, registry)
        installation_id = installation.id
        installation_type = type(installation)
        prepared = prepare_ingress(db, endpoint=address(key), registry=registry)
        event_id = uuid4().hex
        if recorded_origin:
            ((receipt_id, created),) = record_batch(
                db,
                prepared,
                (
                    InboundEvent(
                        provider_event_id=event_id,
                        event_type="received",
                        payload={"value": 1},
                    ),
                ),
            )
        else:
            receipt, created = receive_verified(
                db,
                installation_id=installation.id,
                capability_binding_id=binding.id,
                provider_event_id=event_id,
                event_type="received",
                payload={"value": 1},
            )
            receipt_id = receipt.id
        assert created
        contract = capabilities.get(binding.capability_id)
        descriptor = ProductPortDescriptorSnapshot(
            schema_version="dotmac.io/product-port-descriptor/v3",
            application=contract.owner.application,
            owner_module=contract.owner.module,
            capability_id=contract.capability_id,
            capability_summary=contract.summary,
            contract_version=contract.contract_version,
            destination_binding_id=uuid4(),
            delivery_path="/integration/observations",
            mirror_path="/integration/mirror",
            destination_scope=LocalScope(kind="inbox", ref="origin-test"),
            activation_state="enabled",
            source_revision="a" * 64,
            descriptor_digest="0" * 64,
            wire_schema_version=f"dotmac.io/product-observation/{wire_version}",
            capability_contract=contract,
        )
        descriptor = replace(
            descriptor, descriptor_digest=product_port_descriptor_digest(descriptor)
        )
        reconcile_product_port_descriptor(
            db,
            capability_binding_id=binding.id,
            descriptor=descriptor,
            registry=capabilities,
        )
    claims = ReceiptClaims(sessionmaker(bind=engine), registry=capabilities)
    first = claims.claim(receipt_id=receipt_id)
    assert first is not None
    original = first.origin_evidence
    if not recorded_origin:
        assert original is None
        with Session(engine) as db:
            assert resolve_receipt_origin_evidence(db, receipt_id=receipt_id) is None
        if wire_version == "v2":
            with pytest.raises(DeliveryError, match="history is unknown"):
                build_product_request(first)
        else:
            assert "receipt_origin" not in product_observation_document(
                build_product_request(first)
            )
        engine.dispose()
        return
    assert original is not None
    assert original.verification_origin == prepared.verification_origin
    request = build_product_request(first)
    document = product_observation_document(request)
    assert claims.settle(
        first, ProductOutcome(acceptance=ProductAcceptance.UNAVAILABLE)
    )
    with Session(engine) as db, db.begin():
        installation = db.get(installation_type, installation_id)
        put_config_revision(
            db,
            installation,
            registry=registry,
            config={"variant": "rotated"},
            secret_refs={"signing": "bao://kv/signing"},
        )
        _reenable(db, installation, registry)
        rotated = prepare_ingress(db, endpoint=address(key), registry=registry)
        assert rotated.config_revision_id != prepared.config_revision_id
        assert resolve_receipt_origin_evidence(db, receipt_id=receipt_id) == original
        # The retained receipt, not the later-mutable/deletable revision table,
        # supplies every original field for the actual replay request below.
        db.delete(db.get(ConnectorConfigRevision, prepared.config_revision_id))
    # A retained dirty entity is not persisted proof. This deliberately stages a
    # change the final database immutability trigger would reject on flush. The
    # evidence query must neither autoflush it nor attest it from the identity map.
    with Session(engine) as db:
        dirty = db.get(InboxReceipt, receipt_id)
        assert dirty is not None
        dirty.verification_config_revision_id = rotated.config_revision_id
        dirty.verification_manifest_digest = "b" * 64
        dirty.verification_config_digest = "d" * 64
        assert db.is_modified(dirty)
        assert resolve_receipt_origin_evidence(db, receipt_id=receipt_id) == original
        assert db.is_modified(dirty)
        assert dirty.verification_config_revision_id == rotated.config_revision_id
        db.rollback()
    replay = claims.claim(
        receipt_id=receipt_id, now=datetime.now(UTC) + timedelta(days=1)
    )
    assert replay is not None
    assert replay.origin_evidence == original
    assert product_observation_document(build_product_request(replay)) == document
    assert (
        build_product_request(replay).request_fingerprint == request.request_fingerprint
    )
    if wire_version == "v2":
        assert (
            document["receipt_origin"]["original_config_digest"]
            == prepared.config_digest
        )
        assert document["receipt_origin"]["config_revision_id"] == str(
            prepared.config_revision_id
        )
    else:
        assert "receipt_origin" not in document
    engine.dispose()
