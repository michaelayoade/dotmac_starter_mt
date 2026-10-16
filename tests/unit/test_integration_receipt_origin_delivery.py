"""Hosted checks for explicit original-origin wire negotiation and replay."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

import pytest
from dotmac_integration.execution import ReceiptVerificationOrigin, payload_digest
from dotmac_integration.receipt_delivery import (
    DeliveryError,
    FingerprintConflict,
    ReceiptOriginEvidence,
    build_product_request,
    product_observation_document,
)

from tests.unit.test_integration_receipt_delivery import _claim


def _v2_claim():
    claim = _claim()
    destination = SimpleNamespace(
        **{
            name: getattr(claim.destination, name)
            for name in (
                "capability_binding_id",
                "capability_id",
                "application",
                "scope",
                "contract_version",
                "destination_revision_id",
            )
        },
        product_port=SimpleNamespace(
            wire_schema_version="dotmac.io/product-observation/v2"
        ),
    )
    origin = ReceiptVerificationOrigin(
        installation_id=claim.source.installation_id,
        capability_binding_id=claim.destination.capability_binding_id,
        connector_key=claim.source.connector_key,
        manifest_digest="a" * 64,
        config_revision_id=UUID(int=17),
        config_digest="c" * 64,
        verification_mode="ingress",
    )
    return replace(
        claim,
        destination=destination,
        origin_evidence=ReceiptOriginEvidence(
            receipt_id=claim.receipt_id,
            payload_digest=payload_digest(dict(claim.observation)),
            verification_origin=origin,
        ),
    )


def test_v2_projects_original_receipt_not_current_configuration():
    claim = _v2_claim()
    document = product_observation_document(build_product_request(claim))
    assert document["schema_version"] == "dotmac.io/product-observation/v2"
    assert document["receipt_origin"] == {
        "receipt_id": str(claim.receipt_id),
        "payload_digest": payload_digest(dict(claim.observation)),
        "installation_id": str(claim.source.installation_id),
        "capability_binding_id": str(claim.destination.capability_binding_id),
        "connector_key": claim.source.connector_key,
        "manifest_digest": "a" * 64,
        "original_config_digest": "c" * 64,
        "config_revision_id": str(UUID(int=17)),
        "verification_mode": "ingress",
    }
    replay = replace(
        claim,
        attempt=2,
        stored_fingerprint=build_product_request(claim).request_fingerprint,
    )
    assert product_observation_document(build_product_request(replay)) == document


def test_legacy_v1_document_and_fingerprint_ignore_new_optional_evidence():
    legacy = _claim()
    enriched = replace(legacy, origin_evidence=_v2_claim().origin_evidence)
    assert product_observation_document(
        build_product_request(enriched)
    ) == product_observation_document(build_product_request(legacy))
    assert (
        build_product_request(enriched).request_fingerprint
        == build_product_request(legacy).request_fingerprint
    )
    assert "receipt_origin" not in product_observation_document(
        build_product_request(enriched)
    )


@pytest.mark.parametrize(
    "mutation", ["missing", "receipt", "installation", "binding", "digest"]
)
def test_v2_refuses_unknown_or_inconsistent_original_context(mutation):
    claim = _v2_claim()
    evidence = claim.origin_evidence
    if mutation == "missing":
        evidence = None
    elif mutation == "receipt":
        evidence = replace(evidence, receipt_id=UUID(int=999))
    elif mutation == "digest":
        evidence = replace(evidence, payload_digest="b" * 64)
    else:
        field = (
            "installation_id" if mutation == "installation" else "capability_binding_id"
        )
        evidence = replace(
            evidence,
            verification_origin=replace(
                evidence.verification_origin, **{field: UUID(int=999)}
            ),
        )
    with pytest.raises(DeliveryError):
        build_product_request(replace(claim, origin_evidence=evidence))


def test_mutated_original_origin_conflicts_before_product_call():
    claim = _v2_claim()
    evidence = replace(
        claim.origin_evidence,
        verification_origin=replace(
            claim.origin_evidence.verification_origin, config_revision_id=UUID(int=999)
        ),
    )
    with pytest.raises(FingerprintConflict):
        build_product_request(
            replace(
                claim,
                origin_evidence=evidence,
                stored_fingerprint=build_product_request(claim).request_fingerprint,
            )
        )


def test_unknown_wire_version_refuses_without_downgrade():
    claim = _v2_claim()
    claim.destination.product_port.wire_schema_version = (
        "dotmac.io/product-observation/v99"
    )
    with pytest.raises(DeliveryError):
        build_product_request(claim)


def test_prepared_no_config_is_explicit_null_not_unknown_history():
    claim = _v2_claim()
    evidence = replace(
        claim.origin_evidence,
        verification_origin=replace(
            claim.origin_evidence.verification_origin,
            config_revision_id=None,
            config_digest=None,
        ),
    )
    document = product_observation_document(
        build_product_request(replace(claim, origin_evidence=evidence))
    )
    assert document["receipt_origin"]["config_revision_id"] is None
    assert document["receipt_origin"]["original_config_digest"] is None


def test_existing_explicit_messaging_envelope_keeps_legacy_request_fingerprint():
    claim = _v2_claim()
    claim.destination.product_port.wire_schema_version = (
        "dotmac.io/integrator-observation-envelope/v1"
    )
    request = build_product_request(claim)
    legacy = build_product_request(_claim())
    assert request.request_fingerprint == legacy.request_fingerprint
    assert product_observation_document(request) == product_observation_document(legacy)


@pytest.mark.parametrize(
    "field,value",
    [
        ("manifest_digest", "bad"),
        ("verification_mode", "anything"),
        ("config_revision_id", "bad"),
        ("config_digest", "bad"),
        ("config_digest", None),
        ("connector_key", " bad "),
    ],
)
def test_v2_revalidates_bypass_mutated_nested_origin(field, value):
    claim = _v2_claim()
    request = build_product_request(claim)
    object.__setattr__(claim.origin_evidence.verification_origin, field, value)
    with pytest.raises(DeliveryError):
        product_observation_document(request)


@pytest.mark.parametrize(
    "field,value",
    [
        ("receipt_id", "bad"),
        ("payload_digest", "bad"),
        ("verification_origin", object()),
    ],
)
def test_v2_revalidates_bypass_mutated_evidence(field, value):
    claim = _v2_claim()
    request = build_product_request(claim)
    object.__setattr__(claim.origin_evidence, field, value)
    with pytest.raises(DeliveryError):
        product_observation_document(request)


def test_changed_original_config_digest_conflicts_on_replay():
    claim = _v2_claim()
    evidence = replace(
        claim.origin_evidence,
        verification_origin=replace(
            claim.origin_evidence.verification_origin, config_digest="d" * 64
        ),
    )
    with pytest.raises(FingerprintConflict):
        build_product_request(
            replace(
                claim,
                origin_evidence=evidence,
                stored_fingerprint=build_product_request(claim).request_fingerprint,
            )
        )


def test_uuid_only_original_config_context_is_incomplete():
    claim = _v2_claim()
    with pytest.raises(ValueError, match="must be paired"):
        replace(claim.origin_evidence.verification_origin, config_digest=None)
