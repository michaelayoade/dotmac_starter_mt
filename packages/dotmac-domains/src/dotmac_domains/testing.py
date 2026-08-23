"""Conformance checks for Integrator-backed domain semantic adapters."""

from __future__ import annotations

from datetime import timedelta

from dotmac_domains.contracts import (
    DNS_AUTHORITATIVE_CAPABILITY,
    DNS_OPERATIONS,
    DOMAINS_REGISTRAR_CAPABILITY,
    REGISTRAR_OPERATIONS,
    ApplyDNSRecordSetsV1,
    ConfigureDNSZoneV1,
    DNSAuthoritativeCapabilityV1,
    DNSRecordSetV1,
    DomainContactSetV1,
    DomainContactV1,
    DomainPostalAddressV1,
    ReconcileRegistrarDomainV1,
    RegisterDomainV1,
    RegistrarCapabilityV1,
    RenewDomainV1,
    TransferDirection,
    TransferDomainV1,
    UpdateDomainContactsV1,
    UpdateDomainNameserversV1,
)


class RegistrarConformanceError(AssertionError):
    pass


def _conformance_contact_set() -> DomainContactSetV1:
    address = DomainPostalAddressV1(
        line_one="12 Conformance Street",
        city="Abuja",
        region="FCT",
        postal_code="900001",
        country_code="NG",
    )
    contact = DomainContactV1(
        full_name="Conformance Customer",
        organization="Conformance Limited",
        email="owner@conformance-customer.ng",
        phone="+2348090000000",
        address=address,
    )
    return DomainContactSetV1(
        source_authority="conformance.customer.contacts",
        source_reference="conformance-contact-set",
        source_version="1",
        registrant=contact,
        administrative=contact,
        technical=contact,
        billing=contact,
    )


def check_registrar_capability_v1(candidate: RegistrarCapabilityV1) -> None:
    """Exercise the irreversible acknowledgement/confirmation distinction."""

    if candidate.capability_id != DOMAINS_REGISTRAR_CAPABILITY:
        raise RegistrarConformanceError("candidate declares the wrong capability id")
    if candidate.supported_operations != REGISTRAR_OPERATIONS:
        missing = REGISTRAR_OPERATIONS - candidate.supported_operations
        extra = candidate.supported_operations - REGISTRAR_OPERATIONS
        raise RegistrarConformanceError(
            "candidate operation declaration differs from registrar V1: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    name = "conformance-customer.ng"
    availability = candidate.availability(name)
    if availability.name != name:
        raise RegistrarConformanceError("availability changed the requested name")
    acknowledgement = candidate.register(
        RegisterDomainV1(
            operation_reference="conformance-registration",
            name=name,
            term_months=12,
            contact_set=_conformance_contact_set(),
            nameservers=("ns1.dotmac.ng", "ns2.dotmac.ng"),
            privacy_requested=True,
        )
    )
    if acknowledgement.operation_reference != "conformance-registration":
        raise RegistrarConformanceError("acknowledgement lost operation correlation")
    # The acknowledgement has no Dotmac lifecycle state.  Confirmation comes
    # only through an independently identified observation.
    if hasattr(acknowledgement, "lifecycle_state"):
        raise RegistrarConformanceError(
            "registrar acknowledgement must not assign Dotmac lifecycle state"
        )
    observation = candidate.observe(name)
    if observation.name != name or not observation.provider_event_id:
        raise RegistrarConformanceError("observation is not independently identified")
    if observation.observed_at.tzinfo is None:
        raise RegistrarConformanceError("observation time is not timezone-aware")

    renewal = candidate.renew(
        RenewDomainV1(
            operation_reference="conformance-renewal",
            name=name,
            term_months=12,
            observed_expires_at=availability.observed_at + timedelta(days=360),
        )
    )
    transfers = (
        candidate.transfer(
            TransferDomainV1(
                operation_reference="conformance-transfer-approve-out",
                name=name,
                direction=TransferDirection.APPROVE_OUT,
            )
        ),
        candidate.transfer(
            TransferDomainV1(
                operation_reference="conformance-transfer-cancel",
                name=name,
                direction=TransferDirection.CANCEL,
            )
        ),
    )

    contacts = candidate.update_contacts(
        UpdateDomainContactsV1(
            operation_reference="conformance-contacts",
            name=name,
            contact_set=_conformance_contact_set(),
        )
    )
    nameservers = candidate.update_nameservers(
        UpdateDomainNameserversV1(
            operation_reference="conformance-nameservers",
            name=name,
            nameservers=("ns1.dotmac.ng", "ns2.dotmac.ng"),
        )
    )
    reconciled = candidate.reconcile(
        ReconcileRegistrarDomainV1(
            operation_reference="conformance-reconcile",
            name=name,
        )
    )
    if contacts.operation_reference != "conformance-contacts":
        raise RegistrarConformanceError("contacts lost operation correlation")
    if renewal.operation_reference != "conformance-renewal":
        raise RegistrarConformanceError("renewal lost operation correlation")
    expected_transfer_refs = (
        "conformance-transfer-approve-out",
        "conformance-transfer-cancel",
    )
    if tuple(item.operation_reference for item in transfers) != expected_transfer_refs:
        raise RegistrarConformanceError("transfer lost operation correlation")
    if nameservers.operation_reference != "conformance-nameservers":
        raise RegistrarConformanceError("nameservers lost operation correlation")
    if reconciled.name != name or reconciled.source_mode != "poll":
        raise RegistrarConformanceError("reconcile did not return a poll observation")


class DNSConformanceError(AssertionError):
    pass


def check_dns_authoritative_capability_v1(
    candidate: DNSAuthoritativeCapabilityV1,
) -> None:
    if candidate.capability_id != DNS_AUTHORITATIVE_CAPABILITY:
        raise DNSConformanceError("candidate declares the wrong DNS capability id")
    if candidate.supported_operations != DNS_OPERATIONS:
        raise DNSConformanceError("candidate operation declaration differs from DNS V1")
    zone_name = "conformance-customer.ng"
    zone = candidate.configure_zone(
        ConfigureDNSZoneV1(
            operation_reference="conformance-zone",
            zone_name=zone_name,
            nameservers=("ns1.dotmac.ng", "ns2.dotmac.ng"),
        )
    )
    recordsets = candidate.apply_recordsets(
        ApplyDNSRecordSetsV1(
            operation_reference="conformance-recordsets",
            zone_name=zone_name,
            recordsets=(
                DNSRecordSetV1(
                    owner="@",
                    record_type="A",
                    ttl=300,
                    values=("192.0.2.10",),
                ),
            ),
        )
    )
    observation = candidate.observe(zone_name)
    if zone.operation_reference != "conformance-zone":
        raise DNSConformanceError("zone acknowledgement lost operation correlation")
    if recordsets.operation_reference != "conformance-recordsets":
        raise DNSConformanceError(
            "recordset acknowledgement lost operation correlation"
        )
    if observation.zone_name != zone_name or not observation.provider_event_id:
        raise DNSConformanceError("DNS observation is not independently identified")
    if observation.recordsets_digest is None:
        raise DNSConformanceError("DNS observation omitted applied-state evidence")


__all__ = [
    "DNSConformanceError",
    "RegistrarConformanceError",
    "check_dns_authoritative_capability_v1",
    "check_registrar_capability_v1",
]
