"""Provider-free registrar fake for domain-owner and assembly tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from dotmac_domains.contracts import (
    DNS_AUTHORITATIVE_CAPABILITY,
    DNS_OPERATIONS,
    DOMAINS_REGISTRAR_CAPABILITY,
    REGISTRAR_OPERATIONS,
    ApplyDNSRecordSetsV1,
    ConfigureDNSZoneV1,
    DNSAcknowledgementV1,
    DNSObservationV1,
    DNSRecordSetV1,
    DomainAvailability,
    DomainAvailabilityFactV1,
    DomainObservationV1,
    ReconcileRegistrarDomainV1,
    RegisterDomainV1,
    RegistrarAcknowledgementV1,
    RenewDomainV1,
    TransferDomainV1,
    UpdateDomainContactsV1,
    UpdateDomainNameserversV1,
)


@dataclass(slots=True)
class FakeRegistrarCapabilityV1:
    """Deterministic semantic fake; it performs no provider or network I/O."""

    now: datetime = field(
        default_factory=lambda: datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    )
    capability_id: str = DOMAINS_REGISTRAR_CAPABILITY
    supported_operations: frozenset[str] = REGISTRAR_OPERATIONS
    available_names: set[str] = field(default_factory=set)
    expiry_by_name: dict[str, datetime] = field(default_factory=dict)
    status_by_name: dict[str, tuple[str, ...]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    registration_requests: list[RegisterDomainV1] = field(default_factory=list)
    contact_requests: list[UpdateDomainContactsV1] = field(default_factory=list)
    transfer_requests: list[TransferDomainV1] = field(default_factory=list)

    def availability(self, name: str) -> DomainAvailabilityFactV1:
        self.calls.append(("availability", name))
        return DomainAvailabilityFactV1(
            name=name,
            availability=(
                DomainAvailability.YES
                if name in self.available_names
                else DomainAvailability.NO
            ),
            provider_status="available" if name in self.available_names else "taken",
            premium=False,
            provider_quote=None,
            observed_at=self.now,
        )

    def register(self, request: RegisterDomainV1) -> RegistrarAcknowledgementV1:
        self.calls.append(("registration", request.name))
        self.registration_requests.append(request)
        self.available_names.discard(request.name)
        self.expiry_by_name[request.name] = self.now + timedelta(
            days=30 * request.term_months
        )
        self.status_by_name[request.name] = ("ok",)
        return RegistrarAcknowledgementV1(
            operation_reference=request.operation_reference,
            provider_order_ref=f"fake-order:{request.operation_reference}",
            accepted_at=self.now,
        )

    def renew(self, request: RenewDomainV1) -> RegistrarAcknowledgementV1:
        self.calls.append(("renewal", request.name))
        self.expiry_by_name[request.name] = request.observed_expires_at + timedelta(
            days=30 * request.term_months
        )
        return RegistrarAcknowledgementV1(
            operation_reference=request.operation_reference,
            provider_order_ref=f"fake-renewal:{request.operation_reference}",
            accepted_at=self.now,
        )

    def transfer(self, request: TransferDomainV1) -> RegistrarAcknowledgementV1:
        self.calls.append(("transfer", request.name))
        self.transfer_requests.append(request)
        return RegistrarAcknowledgementV1(
            operation_reference=request.operation_reference,
            provider_order_ref=f"fake-transfer:{request.operation_reference}",
            accepted_at=self.now,
        )

    def update_contacts(
        self, request: UpdateDomainContactsV1
    ) -> RegistrarAcknowledgementV1:
        self.calls.append(("contacts", request.name))
        self.contact_requests.append(request)
        return RegistrarAcknowledgementV1(
            operation_reference=request.operation_reference,
            provider_order_ref=f"fake-contacts:{request.operation_reference}",
            accepted_at=self.now,
        )

    def update_nameservers(
        self, request: UpdateDomainNameserversV1
    ) -> RegistrarAcknowledgementV1:
        self.calls.append(("nameservers", request.name))
        return RegistrarAcknowledgementV1(
            operation_reference=request.operation_reference,
            provider_order_ref=f"fake-nameservers:{request.operation_reference}",
            accepted_at=self.now,
        )

    def observe(self, name: str) -> DomainObservationV1:
        self.calls.append(("observation", name))
        return self._observation(name, event_prefix="fake-observe")

    def reconcile(self, request: ReconcileRegistrarDomainV1) -> DomainObservationV1:
        self.calls.append(("reconcile", request.name))
        return self._observation(request.name, event_prefix="fake-reconcile")

    def _observation(self, name: str, *, event_prefix: str) -> DomainObservationV1:
        return DomainObservationV1(
            name=name,
            observation_kind="registered",
            provider_statuses=self.status_by_name.get(name, ("unknown",)),
            expires_at=self.expiry_by_name.get(name),
            observed_at=self.now,
            provider_event_id=f"{event_prefix}:{name}:{self.now.isoformat()}",
            capability_binding_ref="fake-registrar-binding",
            source_mode="poll",
        )


@dataclass(slots=True)
class FakeDNSAuthoritativeCapabilityV1:
    """Deterministic DNS semantic fake; it performs no provider I/O."""

    now: datetime = field(
        default_factory=lambda: datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    )
    capability_id: str = DNS_AUTHORITATIVE_CAPABILITY
    supported_operations: frozenset[str] = DNS_OPERATIONS
    nameservers_by_zone: dict[str, tuple[str, ...]] = field(default_factory=dict)
    recordsets_by_zone: dict[str, tuple[DNSRecordSetV1, ...]] = field(
        default_factory=dict
    )
    calls: list[tuple[str, str]] = field(default_factory=list)

    def configure_zone(self, request: ConfigureDNSZoneV1) -> DNSAcknowledgementV1:
        self.calls.append(("zone", request.zone_name))
        self.nameservers_by_zone[request.zone_name] = request.nameservers
        return DNSAcknowledgementV1(
            operation_reference=request.operation_reference,
            accepted_at=self.now,
        )

    def apply_recordsets(self, request: ApplyDNSRecordSetsV1) -> DNSAcknowledgementV1:
        self.calls.append(("recordset", request.zone_name))
        self.recordsets_by_zone[request.zone_name] = request.recordsets
        return DNSAcknowledgementV1(
            operation_reference=request.operation_reference,
            accepted_at=self.now,
        )

    def observe(self, zone_name: str) -> DNSObservationV1:
        self.calls.append(("observation", zone_name))
        return DNSObservationV1(
            zone_name=zone_name,
            provider_event_id=f"fake-dns-observe:{zone_name}:{self.now.isoformat()}",
            capability_binding_ref="fake-dns-binding",
            observed_at=self.now,
            nameservers=self.nameservers_by_zone.get(zone_name, ()),
            recordsets=self.recordsets_by_zone.get(zone_name, ()),
            source_mode="poll",
        )


__all__ = ["FakeDNSAuthoritativeCapabilityV1", "FakeRegistrarCapabilityV1"]
