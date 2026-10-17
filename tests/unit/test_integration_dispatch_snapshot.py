"""First-dispatch contracts; runtime validation belongs to hosted CI."""

from dataclasses import replace
from uuid import UUID

import pytest
from dotmac_integration.capability_registry import CapabilityContract, CapabilityOwner
from dotmac_integration.destination_binding import DestinationBinding, LocalScope
from dotmac_integration.receipt_delivery import (
    DeliveryPreparationRefused,
    DeliverySnapshot,
    ProductAcceptance,
    ProductObservationSource,
    deliver_receipt,
)


def _snapshot() -> DeliverySnapshot:
    return DeliverySnapshot(
        source=ProductObservationSource(UUID(int=1), "synthetic"),
        capability=CapabilityContract(
            capability_id="conformance.echo.v1",
            owner=CapabilityOwner("sub", "snapshot"),
            summary="Synthetic snapshot",
            observation_schema={"type": "object"},
        ),
        destination=DestinationBinding(
            UUID(int=2),
            "conformance.echo.v1",
            "sub",
            LocalScope("inbox", "test"),
            1,
            UUID(int=3),
        ),
    )


def test_snapshot_roundtrip_contains_no_observation_or_endpoint() -> None:
    snapshot = _snapshot()
    document = snapshot.document()
    assert DeliverySnapshot.from_document(document) == snapshot
    assert set(document) == {
        "schema_version",
        "state",
        "source",
        "destination",
        "capability",
    }
    assert "observation" not in document
    assert "endpoint" not in repr(document)


@pytest.mark.parametrize(
    "change",
    [
        {"state": "unstarted"},
        {"schema_version": 2},
        {"extra": "secret"},
        {"source": {"installation_id": "bad", "connector_key": "synthetic"}},
    ],
)
def test_snapshot_fails_closed_on_malformed_or_unknown_document(change: dict) -> None:
    with pytest.raises(DeliveryPreparationRefused):
        DeliverySnapshot.from_document(_snapshot().document() | change)


def test_snapshot_contract_version_rejects_bool() -> None:
    document = _snapshot().document()
    document["destination"]["contract_version"] = True
    with pytest.raises(DeliveryPreparationRefused):
        DeliverySnapshot.from_document(document)


def test_snapshot_captures_scope_content_not_only_revision_uuid() -> None:
    original = _snapshot()
    changed = replace(
        original,
        destination=replace(original.destination, scope=LocalScope("inbox", "changed")),
    )
    assert (
        original.destination.destination_revision_id
        == changed.destination.destination_revision_id
    )
    assert original.document() != changed.document()


def test_preparation_refusal_is_a_normal_report_without_gateway_or_settle() -> None:
    class Store:
        def claim(self, **kwargs):
            raise DeliveryPreparationRefused("dispatch_snapshot_unavailable")

        def settle(self, *args):
            pytest.fail("refusal cannot settle a fictitious claim")

    class Gateway:
        def deliver(self, *args):
            pytest.fail("refusal cannot reach network")

    report = deliver_receipt(receipt_id=UUID(int=4), store=Store(), gateway=Gateway())
    assert not report.claimed
    assert report.outcome.acceptance is ProductAcceptance.INDETERMINATE
    assert report.outcome.error_code == "dispatch_snapshot_unavailable"


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_published_v1_v2_golden_document_and_fingerprint_remain_unchanged(version):
    import hashlib
    import json

    from dotmac_integration.receipt_delivery import (
        build_product_request,
        product_observation_document,
    )

    from tests.unit.test_integration_receipt_delivery import _claim
    from tests.unit.test_integration_receipt_origin_delivery import _v2_claim

    claim = _claim() if version == "v1" else _v2_claim()
    expected = {
        "schema_version": f"dotmac.io/product-observation/{version}",
        "capability_id": "messaging.receive.v1",
        "contract_version": 1,
        "source": {
            "installation_id": "00000000-0000-0000-0000-000000000003",
            "connector_key": "synthetic_connector",
        },
        "provider_event_id": "provider-event-7",
        "event_type": "message.received",
        "scope": {"kind": "inbox", "ref": "support"},
        "observation": {"text": "hello", "from": "+2348000000000"},
    }
    # These independent golden inputs pin the published canonical material;
    # adding the snapshot must not add routing provenance to either fingerprint.
    if version == "v2":
        expected["receipt_origin"] = {
            "receipt_id": "00000000-0000-0000-0000-000000000007",
            "payload_digest": hashlib.sha256(
                b'{"from":"+2348000000000","text":"hello"}'
            ).hexdigest(),
            "installation_id": "00000000-0000-0000-0000-000000000003",
            "capability_binding_id": "00000000-0000-0000-0000-000000000001",
            "connector_key": "synthetic_connector",
            "manifest_digest": "a" * 64,
            "original_config_digest": "c" * 64,
            "config_revision_id": "00000000-0000-0000-0000-000000000011",
            "verification_mode": "ingress",
        }
        material = expected
    else:
        material = {
            key: value for key, value in expected.items() if key != "schema_version"
        }
        material["application"] = "sub"
    expected_fingerprint = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    request = build_product_request(claim)
    assert product_observation_document(request) == expected
    assert request.request_fingerprint == expected_fingerprint


def test_snapshot_free_custom_store_keeps_its_structural_compatibility_seam():
    from tests.unit.test_integration_receipt_delivery import (
        _claim,
        _FakeGateway,
        _FakeStore,
    )

    claim = _claim()
    assert claim.delivery_snapshot is None
    store = _FakeStore(claim)
    report = deliver_receipt(
        receipt_id=claim.receipt_id,
        store=store,
        gateway=_FakeGateway(store),
    )
    assert report.claimed
    assert not report.preparation_refused
    assert store.events == ["claim", "deliver", "settle"]
