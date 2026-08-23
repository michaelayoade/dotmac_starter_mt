from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_domains.contracts import (
    ApplyDNSRecordSetsV1,
    ApprovalDecision,
    ApprovalReceipt,
    ContractError,
    DNSRecordSetV1,
    DomainAvailability,
    DomainAvailabilityFactV1,
    DomainObservationV1,
    RegisterDomainV1,
    ReleaseDomain,
    RequestTransferDomain,
    TransferDirection,
    canonical_domain_name,
    release_content_digest,
    transfer_out_content_digest,
)
from dotmac_domains.vocabulary import DomainVocabularyRegistry

NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("Example.COM.", "example.com"),
        ("  xn--n3h.net  ", "xn--n3h.net"),
        ("bücher.example.ng", "xn--bcher-kva.example.ng"),
    ],
)
def test_domain_names_are_canonicalized_once(supplied: str, expected: str) -> None:
    assert canonical_domain_name(supplied) == expected


@pytest.mark.parametrize(
    "supplied",
    [
        "localhost",
        "example.test",
        "*.example.ng",
        "https://example.ng",
        "example.ng:443",
        "-bad.example.ng",
        "bad_.example.ng",
    ],
)
def test_non_registrable_or_routing_inputs_are_refused(supplied: str) -> None:
    with pytest.raises(ContractError):
        canonical_domain_name(supplied)


def test_registrar_observation_requires_aware_domain_time() -> None:
    with pytest.raises(ContractError, match="observed_at must be timezone-aware"):
        DomainObservationV1(
            name="customer.ng",
            observation_kind="registered",
            provider_statuses=("ok",),
            observed_at=NOW.replace(tzinfo=None),
            provider_event_id="event-1",
            capability_binding_ref="binding-1",
        )


def test_availability_unknown_is_not_coerced_to_available() -> None:
    fact = DomainAvailabilityFactV1(
        name="customer.ng",
        availability=DomainAvailability.UNKNOWN,
        provider_status="timeout",
        premium=False,
        provider_quote=None,
        observed_at=NOW,
    )
    assert fact.availability is DomainAvailability.UNKNOWN
    assert not fact.is_available


def test_open_vocabulary_accepts_only_registered_members() -> None:
    registry = DomainVocabularyRegistry(
        observation_kinds=("registered",),
        consequence_kinds=("renewal_review",),
    )
    registry.require_observation_kind("registered")
    with pytest.raises(KeyError, match="not registered"):
        registry.require_observation_kind("registry_added_later")

    expanded = registry.extended(observation_kinds=("registry_added_later",))
    expanded.require_observation_kind("registry_added_later")
    assert registry.observation_kinds == frozenset({"registered"})


def test_release_approval_is_bound_to_exact_content_and_version() -> None:
    service_id = uuid4()
    request = ReleaseDomain(
        domain_service_id=service_id,
        expected_version=7,
        reason_code="customer_requested_transfer",
        requested_at=NOW,
        approval=None,
    )
    digest = release_content_digest("customer.ng", request)
    approved = ApprovalReceipt(
        policy_code="domains.release",
        policy_version=3,
        content_digest=digest,
        decision=ApprovalDecision.APPROVED,
        decided_at=NOW,
        decision_reference="approval-7",
    )
    approved_request = ReleaseDomain(
        domain_service_id=service_id,
        expected_version=7,
        reason_code="customer_requested_transfer",
        requested_at=NOW,
        approval=approved,
    )
    assert release_content_digest("customer.ng", approved_request) == digest

    changed = ReleaseDomain(
        domain_service_id=service_id,
        expected_version=8,
        reason_code="customer_requested_transfer",
        requested_at=NOW,
        approval=approved,
    )
    assert release_content_digest("customer.ng", changed) != digest


def test_transfer_out_approval_has_a_published_exact_content_digest() -> None:
    request = RequestTransferDomain(
        domain_service_id=uuid4(),
        direction=TransferDirection.APPROVE_OUT,
        requested_at=NOW,
        expected_version=4,
    )
    digest = transfer_out_content_digest("customer.ng", request)
    assert len(digest) == 64
    with pytest.raises(ContractError, match="only a transfer-out"):
        transfer_out_content_digest(
            "customer.ng",
            RequestTransferDomain(
                domain_service_id=request.domain_service_id,
                direction=TransferDirection.CANCEL,
                requested_at=NOW,
            ),
        )


def test_registration_contract_contains_no_provider_identity() -> None:
    request = RegisterDomainV1(
        operation_reference="operation-1",
        name="customer.ng",
        term_months=12,
        contact_set_ref="contacts-1",
        nameserver_set_ref="nameservers-1",
        privacy_requested=True,
    )
    assert request.name == "customer.ng"
    assert not hasattr(request, "provider")
    assert not hasattr(request, "registrar")


def test_dns_contract_accepts_service_labels_but_refuses_duplicate_recordsets() -> None:
    record = DNSRecordSetV1(
        owner="_dmarc.Customer.NG.",
        record_type="txt",
        ttl=300,
        values=("v=DMARC1; p=none",),
    )
    assert record.owner == "_dmarc.customer.ng"
    assert record.record_type == "TXT"
    with pytest.raises(ContractError, match="unique by owner/type"):
        ApplyDNSRecordSetsV1(
            operation_reference="dns-recordsets-1",
            zone_name="customer.ng",
            recordsets=(record, record),
        )
