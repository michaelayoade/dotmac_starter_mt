from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_domains.contracts import (
    Actor,
    ApplyDNSRecordSetsV1,
    ContractError,
    DNSRecordSetV1,
    DNSObservationV1,
    DomainAvailability,
    DomainAvailabilityFactV1,
    DomainContactSetV1,
    DomainContactV1,
    DomainContactsIntent,
    DomainDNSRecordsetsIntent,
    DomainDNSZoneIntent,
    DomainNameserversIntent,
    DomainObservationV1,
    DomainPostalAddressV1,
    RegisterDomainV1,
    RequestTransferDomain,
    SetDomainIntent,
    TransferDirection,
    TransferDomainV1,
    canonical_domain_name,
    canonical_recordsets_digest,
    transfer_out_content_digest,
)
from dotmac_domains.vocabulary import DomainVocabularyRegistry

NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


def _contact_set(*, email: str = "owner@example.ng") -> DomainContactSetV1:
    address = DomainPostalAddressV1(
        line_one="12 Domain Street",
        city="Abuja",
        region="FCT",
        postal_code="900001",
        country_code="NG",
    )
    registrant = DomainContactV1(
        full_name="Example Owner",
        organization="Example Limited",
        email=email,
        phone="+2348090000000",
        address=address,
    )
    return DomainContactSetV1(
        source_authority="cloud.customer.contacts",
        source_reference="contact-set:1",
        source_version="7",
        registrant=registrant,
        administrative=registrant,
        technical=registrant,
        billing=registrant,
    )


def test_cloud_user_actor_is_bound_to_the_same_party_identity() -> None:
    party_id = uuid4()
    actor = Actor(
        actor_type="user",
        actor_id=str(party_id),
        actor_party_id=party_id,
    )
    assert actor.actor_id == str(actor.actor_party_id)
    with pytest.raises(ContractError, match="same Party"):
        Actor(actor_type="user", actor_id="other", actor_party_id=party_id)


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


def test_transfer_out_approval_has_a_published_exact_content_digest() -> None:
    service_id = uuid4()
    request = RequestTransferDomain(
        domain_service_id=service_id,
        direction=TransferDirection.APPROVE_OUT,
        requested_at=NOW,
        expected_version=4,
    )
    digest = transfer_out_content_digest("customer.ng", request)
    assert len(digest) == 64
    assert digest != transfer_out_content_digest(
        "customer.ng",
        RequestTransferDomain(
            domain_service_id=service_id,
            direction=TransferDirection.APPROVE_OUT,
            requested_at=NOW.replace(second=1),
            expected_version=4,
        ),
    )
    assert digest != transfer_out_content_digest(
        "customer.ng",
        RequestTransferDomain(
            domain_service_id=service_id,
            direction=TransferDirection.APPROVE_OUT,
            requested_at=NOW,
            expected_version=5,
        ),
    )
    with pytest.raises(ContractError, match="only a transfer-out"):
        transfer_out_content_digest(
            "customer.ng",
            RequestTransferDomain(
                domain_service_id=request.domain_service_id,
                direction=TransferDirection.CANCEL,
                requested_at=NOW,
            ),
        )


def test_transfer_contract_has_no_secret_or_owner_local_reference() -> None:
    assert "auth_code_ref" not in TransferDomainV1.__dataclass_fields__


def test_desired_state_commands_are_structurally_typed_by_provider_operation() -> None:
    service_id = uuid4()
    contacts = SetDomainIntent(
        domain_service_id=service_id,
        intent=DomainContactsIntent(contact_set=_contact_set()),
        requested_at=NOW,
    )
    nameservers = SetDomainIntent(
        domain_service_id=service_id,
        intent=DomainNameserversIntent(nameservers=("NS1.DOTMAC.NG.", "ns2.dotmac.ng")),
        requested_at=NOW,
    )
    zone = SetDomainIntent(
        domain_service_id=service_id,
        intent=DomainDNSZoneIntent(nameservers=("ns1.dotmac.ng", "ns2.dotmac.ng")),
        requested_at=NOW,
    )
    recordsets = SetDomainIntent(
        domain_service_id=service_id,
        intent=DomainDNSRecordsetsIntent(
            recordsets=(
                DNSRecordSetV1(
                    owner="www",
                    record_type="A",
                    ttl=300,
                    values=("192.0.2.10",),
                ),
            )
        ),
        requested_at=NOW,
    )

    assert contacts.intent_kind == "contacts"
    assert contacts.content["contact_set"] == _contact_set().to_payload()
    assert (
        contacts.content["contact_content_digest"]
        == _contact_set().contact_content_digest
    )
    assert contacts.content["provenance_digest"] == _contact_set().provenance_digest
    assert nameservers.intent_kind == "nameservers"
    assert nameservers.content == {"nameservers": ["ns1.dotmac.ng", "ns2.dotmac.ng"]}
    assert zone.intent_kind == "dns_zone"
    assert recordsets.intent_kind == "dns_recordset"
    assert recordsets.content["recordsets"] == [
        {
            "owner": "www",
            "record_type": "A",
            "ttl": 300,
            "values": ["192.0.2.10"],
        }
    ]


def test_registration_contract_contains_no_provider_identity() -> None:
    request = RegisterDomainV1(
        operation_reference="operation-1",
        name="customer.ng",
        term_months=12,
        contact_set=_contact_set(),
        nameservers=("ns1.dotmac.ng", "ns2.dotmac.ng"),
        privacy_requested=True,
    )
    assert request.name == "customer.ng"
    assert not hasattr(request, "provider")
    assert not hasattr(request, "registrar")


def test_contact_snapshot_is_closed_owner_digested_and_provider_resolvable() -> None:
    original = _contact_set()
    changed = _contact_set(email="changed@example.ng")

    assert original.contact_content_digest != changed.contact_content_digest
    payload = RegisterDomainV1(
        operation_reference="operation-1",
        name="customer.ng",
        term_months=12,
        contact_set=original,
        nameservers=("ns1.dotmac.ng", "ns2.dotmac.ng"),
        privacy_requested=True,
    ).to_payload()
    decoded = RegisterDomainV1.from_payload(payload)
    assert decoded.contact_set == original
    assert decoded.nameservers == ("ns1.dotmac.ng", "ns2.dotmac.ng")

    planted_reference = dict(payload)
    planted_reference.pop("contact_set")
    planted_reference["contact_set_ref"] = "local-intent-row"
    with pytest.raises(ContractError, match="exact fields"):
        RegisterDomainV1.from_payload(planted_reference)

    planted_secret = dict(payload)
    planted_secret["auth_code"] = "must-not-be-in-a-provider-event"
    with pytest.raises(ContractError, match="exact fields"):
        RegisterDomainV1.from_payload(planted_secret)

    planted_digest = dict(payload)
    planted_contact_set = dict(original.to_payload())
    planted_contact_set["content_digest"] = original.contact_content_digest
    planted_digest["contact_set"] = planted_contact_set
    with pytest.raises(ContractError, match="exact fields"):
        RegisterDomainV1.from_payload(planted_digest)


def test_contact_provider_digest_excludes_provenance() -> None:
    original = _contact_set()
    same_values_new_provenance = DomainContactSetV1(
        source_authority=original.source_authority,
        source_reference="contact-set:2",
        source_version="8",
        registrant=original.registrant,
        administrative=original.administrative,
        technical=original.technical,
        billing=original.billing,
    )
    assert (
        original.contact_content_digest
        == same_values_new_provenance.contact_content_digest
    )
    assert original.provenance_digest != same_values_new_provenance.provenance_digest


def test_dns_observation_digest_is_canonical_and_contains_values() -> None:
    first = DNSRecordSetV1(
        "www", "A", 300, ("192.0.2.10", "192.0.2.11")
    )
    first_reordered = DNSRecordSetV1(
        "www", "A", 300, ("192.0.2.11", "192.0.2.10")
    )
    second = DNSRecordSetV1("@", "MX", 300, ("10 mail.customer.ng",))
    assert canonical_recordsets_digest((first, second)) == canonical_recordsets_digest(
        (second, first_reordered)
    )
    observation = DNSObservationV1(
        zone_name="customer.ng",
        provider_event_id="dns-event-1",
        capability_binding_ref="dns-binding-1",
        observed_at=NOW,
        recordsets=(first, second),
        source_mode="poll",
    )
    assert observation.recordsets == (first, second)


def test_dns_contract_accepts_service_labels_but_refuses_duplicate_recordsets() -> None:
    record = DNSRecordSetV1(
        owner="_dmarc.Customer.NG.",
        record_type="txt",
        ttl=300,
        values=("v=DMARC1; p=none",),
    )
    assert record.owner == "_dmarc.customer.ng"
    assert record.record_type == "TXT"
    request = ApplyDNSRecordSetsV1(
        operation_reference="dns-recordsets-1",
        zone_name="customer.ng",
        recordsets=(record,),
    )
    assert ApplyDNSRecordSetsV1.from_payload(request.to_payload()) == request
    with pytest.raises(ContractError, match="unique by owner/type"):
        ApplyDNSRecordSetsV1(
            operation_reference="dns-recordsets-1",
            zone_name="customer.ng",
            recordsets=(record, record),
        )
