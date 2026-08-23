"""Provider-neutral domain commands, observations, outcomes and capability V1.

These types define Dotmac meaning.  They contain no registrar, DNS vendor,
billing engine or product identity.  Connector plugins translate their wire
formats into these contracts; the Cloud assembly moves them between the
Integrator and this owner.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from dotmac_kernel.money import Money

from dotmac_domains.vocabulary import active_domain_vocabulary

DOMAINS_REGISTRAR_CAPABILITY = "domains.registrar.v1"
DNS_AUTHORITATIVE_CAPABILITY = "dns.authoritative.v1"

REGISTRAR_OPERATIONS = frozenset(
    {
        "availability",
        "registration",
        "renewal",
        "transfer",
        "contacts",
        "nameservers",
        "observation",
        "reconcile",
    }
)
DNS_OPERATIONS = frozenset({"zone", "recordset", "observation"})


class ContractError(ValueError):
    """A malformed domain contract value."""


class DomainAvailability(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class DomainLifecycleState(StrEnum):
    REGISTRATION_REQUESTED = "registration_requested"
    ACTIVE = "active"
    RENEWAL_REQUESTED = "renewal_requested"
    TRANSFER_IN_REQUESTED = "transfer_in_requested"
    TRANSFER_OUT_REQUESTED = "transfer_out_requested"
    RELEASE_REQUESTED = "release_requested"
    EXPIRED = "expired"
    REDEMPTION = "redemption"
    RELEASED = "released"
    UNKNOWN = "unknown"


class DomainCommandKind(StrEnum):
    REGISTRATION = "registration"
    RENEWAL = "renewal"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_CANCEL = "transfer_cancel"
    RELEASE = "release"
    CONTACTS = "contacts"
    NAMESERVERS = "nameservers"
    DNS_INTENT = "dns_intent"
    CONSEQUENCE = "consequence"


class OutcomeKind(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    REFUSED = "refused"


class OutcomeClass(StrEnum):
    SUCCEEDED = "succeeded"
    RETRYABLE = "retryable"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    TERMINAL = "terminal"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REFUSED = "refused"


class TransferDirection(StrEnum):
    IN = "in"
    APPROVE_OUT = "approve_out"
    CANCEL = "cancel"


def _required(name: str, value: str, limit: int = 255) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise ContractError(f"{name} is required and must be at most {limit} chars")
    return normalized


def _aware(name: str, value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ContractError(f"{name} must be timezone-aware")


def _digest(name: str, value: str) -> str:
    normalized = value.removeprefix("sha256:").lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ContractError(f"{name} must be a SHA-256 hex digest")
    return normalized


def fingerprint(payload: object) -> str:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    elif hasattr(payload, "__dataclass_fields__"):
        payload = asdict(payload)  # type: ignore[arg-type]
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def canonical_domain_name(value: str) -> str:
    """Return lower-case IDNA ASCII or refuse a URL/routing/reserved input."""

    supplied = value.strip().rstrip(".")
    if (
        not supplied
        or "://" in supplied
        or "/" in supplied
        or ":" in supplied
        or supplied.startswith("*.")
    ):
        raise ContractError("name must be a registrable domain, not a URL or route")
    try:
        canonical = supplied.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ContractError("name is not valid IDNA") from exc
    if len(canonical) > 253:
        raise ContractError("name must be at most 253 ASCII characters")
    labels = canonical.split(".")
    if len(labels) < 2:
        raise ContractError("name must contain a registrable label and suffix")
    for label in labels:
        if (
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or any(not (ch.isalnum() or ch == "-") for ch in label)
        ):
            raise ContractError(f"name contains an invalid label {label!r}")
    try:
        ipaddress.ip_address(canonical)
    except ValueError:
        pass
    else:
        raise ContractError("an IP address is not a registrable domain")
    reserved = ("localhost", "local", "invalid", "test", "example")
    if labels[-1] in reserved or canonical == "localhost":
        raise ContractError("reserved domain names cannot be registered")
    return canonical


def _nameservers(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(canonical_domain_name(value) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ContractError("nameservers must be unique")
    return normalized


@dataclass(frozen=True, slots=True)
class DomainAvailabilityFactV1:
    name: str
    availability: DomainAvailability
    provider_status: str
    premium: bool
    provider_quote: Money | None
    observed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", canonical_domain_name(self.name))
        object.__setattr__(
            self, "provider_status", _required("provider_status", self.provider_status)
        )
        _aware("observed_at", self.observed_at)

    @property
    def is_available(self) -> bool:
        return self.availability is DomainAvailability.YES


@dataclass(frozen=True, slots=True)
class RegisterDomainV1:
    operation_reference: str
    name: str
    term_months: int
    contact_set_ref: str
    nameserver_set_ref: str
    privacy_requested: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_reference",
            _required("operation_reference", self.operation_reference, 120),
        )
        object.__setattr__(self, "name", canonical_domain_name(self.name))
        if self.term_months <= 0 or self.term_months > 120:
            raise ContractError("term_months must be between 1 and 120")
        object.__setattr__(
            self, "contact_set_ref", _required("contact_set_ref", self.contact_set_ref)
        )
        object.__setattr__(
            self,
            "nameserver_set_ref",
            _required("nameserver_set_ref", self.nameserver_set_ref),
        )


@dataclass(frozen=True, slots=True)
class RenewDomainV1:
    operation_reference: str
    name: str
    term_months: int
    observed_expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_reference",
            _required("operation_reference", self.operation_reference, 120),
        )
        object.__setattr__(self, "name", canonical_domain_name(self.name))
        if self.term_months <= 0 or self.term_months > 120:
            raise ContractError("term_months must be between 1 and 120")
        _aware("observed_expires_at", self.observed_expires_at)


@dataclass(frozen=True, slots=True)
class TransferDomainV1:
    operation_reference: str
    name: str
    direction: TransferDirection
    auth_code_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_reference",
            _required("operation_reference", self.operation_reference, 120),
        )
        object.__setattr__(self, "name", canonical_domain_name(self.name))
        if self.direction is TransferDirection.IN and self.auth_code_ref is None:
            raise ContractError("transfer-in requires an auth_code_ref")
        if self.auth_code_ref is not None:
            object.__setattr__(
                self, "auth_code_ref", _required("auth_code_ref", self.auth_code_ref)
            )


@dataclass(frozen=True, slots=True)
class UpdateDomainContactsV1:
    operation_reference: str
    name: str
    contact_set_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_reference",
            _required("operation_reference", self.operation_reference, 120),
        )
        object.__setattr__(self, "name", canonical_domain_name(self.name))
        object.__setattr__(
            self, "contact_set_ref", _required("contact_set_ref", self.contact_set_ref)
        )


@dataclass(frozen=True, slots=True)
class UpdateDomainNameserversV1:
    operation_reference: str
    name: str
    nameservers: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_reference",
            _required("operation_reference", self.operation_reference, 120),
        )
        object.__setattr__(self, "name", canonical_domain_name(self.name))
        object.__setattr__(self, "nameservers", _nameservers(self.nameservers))


@dataclass(frozen=True, slots=True)
class ReconcileRegistrarDomainV1:
    operation_reference: str
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_reference",
            _required("operation_reference", self.operation_reference, 120),
        )
        object.__setattr__(self, "name", canonical_domain_name(self.name))


@dataclass(frozen=True, slots=True)
class RegistrarAcknowledgementV1:
    operation_reference: str
    provider_order_ref: str
    accepted_at: datetime
    provider_charge: Money | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_reference",
            _required("operation_reference", self.operation_reference, 120),
        )
        object.__setattr__(
            self,
            "provider_order_ref",
            _required("provider_order_ref", self.provider_order_ref),
        )
        _aware("accepted_at", self.accepted_at)


@dataclass(frozen=True, slots=True)
class DomainObservationV1:
    name: str
    observation_kind: str
    provider_statuses: tuple[str, ...]
    observed_at: datetime
    provider_event_id: str
    capability_binding_ref: str
    expires_at: datetime | None = None
    redemption_ends_at: datetime | None = None
    nameservers: tuple[str, ...] = ()
    contact_set_digest: str | None = None
    source_mode: str = "ingress"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", canonical_domain_name(self.name))
        active_domain_vocabulary().require_observation_kind(self.observation_kind)
        object.__setattr__(
            self,
            "provider_event_id",
            _required("provider_event_id", self.provider_event_id),
        )
        object.__setattr__(
            self,
            "capability_binding_ref",
            _required("capability_binding_ref", self.capability_binding_ref),
        )
        if self.source_mode not in {"ingress", "poll"}:
            raise ContractError("source_mode must be ingress or poll")
        _aware("observed_at", self.observed_at)
        _aware("expires_at", self.expires_at)
        _aware("redemption_ends_at", self.redemption_ends_at)
        object.__setattr__(self, "nameservers", _nameservers(self.nameservers))
        if self.contact_set_digest is not None:
            object.__setattr__(
                self,
                "contact_set_digest",
                _digest("contact_set_digest", self.contact_set_digest),
            )


_DNS_RECORD_TYPES = frozenset(
    {"A", "AAAA", "CAA", "CNAME", "MX", "NS", "PTR", "SRV", "TXT"}
)


def canonical_dns_owner(value: str) -> str:
    """Canonicalize a DNS owner while permitting service-label underscores."""

    supplied = value.strip().rstrip(".").lower()
    if supplied == "@":
        return supplied
    if not supplied or "://" in supplied or "/" in supplied or ":" in supplied:
        raise ContractError("DNS owner must be a name, not a URL or route")
    if len(supplied) > 253:
        raise ContractError("DNS owner must be at most 253 characters")
    for label in supplied.split("."):
        if (
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or any(
                not (character.isalnum() or character in {"-", "_"})
                for character in label
            )
        ):
            raise ContractError(f"DNS owner contains an invalid label {label!r}")
    return supplied


@dataclass(frozen=True, slots=True)
class DNSRecordSetV1:
    owner: str
    record_type: str
    ttl: int
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", canonical_dns_owner(self.owner))
        normalized_type = self.record_type.strip().upper()
        if normalized_type not in _DNS_RECORD_TYPES:
            raise ContractError(f"unsupported DNS record type {self.record_type!r}")
        object.__setattr__(self, "record_type", normalized_type)
        if self.ttl < 30:
            raise ContractError("DNS ttl must be at least 30 seconds")
        normalized_values = tuple(
            _required("DNS value", value, 4096) for value in self.values
        )
        if not normalized_values or len(normalized_values) != len(
            set(normalized_values)
        ):
            raise ContractError("DNS record values must be non-empty and unique")
        object.__setattr__(self, "values", normalized_values)


@dataclass(frozen=True, slots=True)
class ConfigureDNSZoneV1:
    operation_reference: str
    zone_name: str
    nameservers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_reference",
            _required("operation_reference", self.operation_reference, 120),
        )
        object.__setattr__(self, "zone_name", canonical_domain_name(self.zone_name))
        object.__setattr__(self, "nameservers", _nameservers(self.nameservers))


@dataclass(frozen=True, slots=True)
class ApplyDNSRecordSetsV1:
    operation_reference: str
    zone_name: str
    recordsets: tuple[DNSRecordSetV1, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_reference",
            _required("operation_reference", self.operation_reference, 120),
        )
        object.__setattr__(self, "zone_name", canonical_domain_name(self.zone_name))
        identities = tuple(
            (record.owner, record.record_type) for record in self.recordsets
        )
        if not identities or len(identities) != len(set(identities)):
            raise ContractError(
                "DNS recordsets must be non-empty and unique by owner/type"
            )


@dataclass(frozen=True, slots=True)
class DNSAcknowledgementV1:
    operation_reference: str
    accepted_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_reference",
            _required("operation_reference", self.operation_reference, 120),
        )
        _aware("accepted_at", self.accepted_at)


@dataclass(frozen=True, slots=True)
class DNSObservationV1:
    zone_name: str
    provider_event_id: str
    capability_binding_ref: str
    observed_at: datetime
    nameservers: tuple[str, ...] = ()
    recordsets_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "zone_name", canonical_domain_name(self.zone_name))
        object.__setattr__(
            self,
            "provider_event_id",
            _required("provider_event_id", self.provider_event_id),
        )
        object.__setattr__(
            self,
            "capability_binding_ref",
            _required("capability_binding_ref", self.capability_binding_ref),
        )
        object.__setattr__(self, "nameservers", _nameservers(self.nameservers))
        if self.recordsets_digest is not None:
            object.__setattr__(
                self,
                "recordsets_digest",
                _digest("recordsets_digest", self.recordsets_digest),
            )
        _aware("observed_at", self.observed_at)


@dataclass(frozen=True, slots=True)
class ApprovalReceipt:
    policy_code: str
    policy_version: int
    content_digest: str
    decision: ApprovalDecision
    decided_at: datetime
    decision_reference: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "policy_code", _required("policy_code", self.policy_code)
        )
        if self.policy_version <= 0:
            raise ContractError("policy_version must be positive")
        object.__setattr__(
            self, "content_digest", _digest("content_digest", self.content_digest)
        )
        _aware("decided_at", self.decided_at)
        object.__setattr__(
            self,
            "decision_reference",
            _required("decision_reference", self.decision_reference),
        )


@dataclass(frozen=True, slots=True)
class RegisterDomain:
    name: str
    order_line_ref: str
    offer_version_ref: str
    term_months: int
    contact_set_ref: str
    nameservers: tuple[str, ...]
    privacy_requested: bool
    commercial_renewal_at: datetime
    requested_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", canonical_domain_name(self.name))
        object.__setattr__(
            self, "order_line_ref", _required("order_line_ref", self.order_line_ref)
        )
        object.__setattr__(
            self,
            "offer_version_ref",
            _required("offer_version_ref", self.offer_version_ref),
        )
        object.__setattr__(
            self, "contact_set_ref", _required("contact_set_ref", self.contact_set_ref)
        )
        object.__setattr__(self, "nameservers", _nameservers(self.nameservers))
        if self.term_months <= 0 or self.term_months > 120:
            raise ContractError("term_months must be between 1 and 120")
        _aware("commercial_renewal_at", self.commercial_renewal_at)
        _aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class RenewDomain:
    domain_service_id: UUID
    term_months: int
    coverage_reference: str
    commercial_renewal_at: datetime
    expected_registrar_expiry: datetime
    requested_at: datetime

    def __post_init__(self) -> None:
        if self.term_months <= 0 or self.term_months > 120:
            raise ContractError("term_months must be between 1 and 120")
        object.__setattr__(
            self,
            "coverage_reference",
            _required("coverage_reference", self.coverage_reference),
        )
        _aware("commercial_renewal_at", self.commercial_renewal_at)
        _aware("expected_registrar_expiry", self.expected_registrar_expiry)
        _aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class ReleaseDomain:
    domain_service_id: UUID
    expected_version: int
    reason_code: str
    requested_at: datetime
    approval: ApprovalReceipt | None

    def __post_init__(self) -> None:
        if self.expected_version < 0:
            raise ContractError("expected_version must be non-negative")
        object.__setattr__(
            self, "reason_code", _required("reason_code", self.reason_code)
        )
        _aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class SetDomainIntent:
    domain_service_id: UUID
    intent_kind: str
    content: dict[str, object]
    requested_at: datetime

    def __post_init__(self) -> None:
        if self.intent_kind not in {"contacts", "nameservers", "dns"}:
            raise ContractError("intent_kind must be contacts, nameservers or dns")
        _aware("requested_at", self.requested_at)
        # JSON serialisability and a deterministic digest are part of the contract.
        fingerprint(self.content)


@dataclass(frozen=True, slots=True)
class ConsequenceRequest:
    domain_service_id: UUID
    consequence_kind: str
    source_owner: str
    source_reference: str
    reason_code: str
    requested_at: datetime

    def __post_init__(self) -> None:
        active_domain_vocabulary().require_consequence_kind(self.consequence_kind)
        object.__setattr__(
            self, "source_owner", _required("source_owner", self.source_owner)
        )
        object.__setattr__(
            self,
            "source_reference",
            _required("source_reference", self.source_reference),
        )
        object.__setattr__(
            self, "reason_code", _required("reason_code", self.reason_code)
        )
        _aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class ClearDomainHold:
    domain_service_id: UUID
    hold_code: str
    source_owner: str
    source_reference: str
    reason_code: str
    requested_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "hold_code", _required("hold_code", self.hold_code, 120)
        )
        object.__setattr__(
            self, "source_owner", _required("source_owner", self.source_owner, 120)
        )
        object.__setattr__(
            self,
            "source_reference",
            _required("source_reference", self.source_reference),
        )
        object.__setattr__(
            self, "reason_code", _required("reason_code", self.reason_code, 160)
        )
        _aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class RequestTransferDomain:
    domain_service_id: UUID
    direction: TransferDirection
    requested_at: datetime
    auth_code_ref: str | None = None
    approval: ApprovalReceipt | None = None
    expected_version: int | None = None

    def __post_init__(self) -> None:
        _aware("requested_at", self.requested_at)
        if self.direction is TransferDirection.IN and self.auth_code_ref is None:
            raise ContractError("transfer-in requires an auth_code_ref")
        if self.auth_code_ref is not None:
            object.__setattr__(
                self, "auth_code_ref", _required("auth_code_ref", self.auth_code_ref)
            )
        if self.direction is TransferDirection.APPROVE_OUT:
            if self.expected_version is None:
                raise ContractError("transfer-out requires expected_version")
            if self.expected_version < 0:
                raise ContractError("expected_version must be non-negative")


@dataclass(frozen=True, slots=True)
class RecordRegistrarOutcome:
    domain_command_id: UUID
    evidence_key: str
    outcome_kind: OutcomeKind
    outcome_class: OutcomeClass
    occurred_at: datetime
    provider_reference: str | None = None
    reason_code: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_key", _required("evidence_key", self.evidence_key)
        )
        if self.provider_reference is not None:
            object.__setattr__(
                self,
                "provider_reference",
                _required("provider_reference", self.provider_reference),
            )
        if self.reason_code is not None:
            object.__setattr__(
                self, "reason_code", _required("reason_code", self.reason_code)
            )
        _aware("occurred_at", self.occurred_at)
        fingerprint(self.details)


@dataclass(frozen=True, slots=True)
class Actor:
    actor_type: str
    actor_id: str | None
    actor_label: str | None = None
    actor_party_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class DomainDrift:
    commercial_renewal_at: datetime | None
    registrar_expires_at: datetime | None
    expiry_disagrees: bool
    nameservers_disagree: bool
    contacts_disagree: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DomainCommandReceipt:
    domain_service_id: UUID
    command_id: UUID
    command_kind: DomainCommandKind
    lifecycle_state: DomainLifecycleState
    replayed: bool


@dataclass(frozen=True, slots=True)
class ObservationReceipt:
    observation_id: UUID
    name: str
    duplicate: bool


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    domain_service_id: UUID
    previous_state: DomainLifecycleState
    current_state: DomainLifecycleState
    observation_id: UUID | None
    changed: bool
    drift: DomainDrift


@dataclass(frozen=True, slots=True)
class ConsequenceOutcome:
    domain_service_id: UUID
    consequence_kind: str
    decision: str
    reason_code: str
    command_id: UUID
    replayed: bool


def release_content_digest(name: str, command: ReleaseDomain) -> str:
    payload = {
        "operation": "release",
        "domain_service_id": str(command.domain_service_id),
        "name": canonical_domain_name(name),
        "expected_version": command.expected_version,
        "reason_code": command.reason_code,
    }
    return fingerprint(payload)


def transfer_out_content_digest(name: str, command: RequestTransferDomain) -> str:
    if command.direction is not TransferDirection.APPROVE_OUT:
        raise ContractError("only a transfer-out request has approval content")
    payload = {
        "operation": "transfer_out",
        "domain_service_id": str(command.domain_service_id),
        "name": canonical_domain_name(name),
        "expected_version": command.expected_version,
    }
    return fingerprint(payload)


class RegistrarCapabilityV1(Protocol):
    """Semantic port implemented by an Integrator-backed assembly adapter."""

    capability_id: str
    supported_operations: frozenset[str]

    def availability(self, name: str) -> DomainAvailabilityFactV1: ...

    def register(self, request: RegisterDomainV1) -> RegistrarAcknowledgementV1: ...

    def renew(self, request: RenewDomainV1) -> RegistrarAcknowledgementV1: ...

    def transfer(self, request: TransferDomainV1) -> RegistrarAcknowledgementV1: ...

    def update_contacts(
        self, request: UpdateDomainContactsV1
    ) -> RegistrarAcknowledgementV1: ...

    def update_nameservers(
        self, request: UpdateDomainNameserversV1
    ) -> RegistrarAcknowledgementV1: ...

    def observe(self, name: str) -> DomainObservationV1: ...

    def reconcile(self, request: ReconcileRegistrarDomainV1) -> DomainObservationV1: ...


class DNSAuthoritativeCapabilityV1(Protocol):
    """Semantic authoritative-DNS port; transport remains in Integrator."""

    capability_id: str
    supported_operations: frozenset[str]

    def configure_zone(self, request: ConfigureDNSZoneV1) -> DNSAcknowledgementV1: ...

    def apply_recordsets(
        self, request: ApplyDNSRecordSetsV1
    ) -> DNSAcknowledgementV1: ...

    def observe(self, zone_name: str) -> DNSObservationV1: ...


__all__ = [
    "DNS_AUTHORITATIVE_CAPABILITY",
    "DNS_OPERATIONS",
    "DOMAINS_REGISTRAR_CAPABILITY",
    "REGISTRAR_OPERATIONS",
    "Actor",
    "ApplyDNSRecordSetsV1",
    "ApprovalDecision",
    "ApprovalReceipt",
    "ClearDomainHold",
    "ConsequenceRequest",
    "ConsequenceOutcome",
    "ContractError",
    "ConfigureDNSZoneV1",
    "DNSAcknowledgementV1",
    "DNSAuthoritativeCapabilityV1",
    "DNSObservationV1",
    "DNSRecordSetV1",
    "DomainAvailability",
    "DomainAvailabilityFactV1",
    "DomainCommandKind",
    "DomainCommandReceipt",
    "DomainDrift",
    "DomainLifecycleState",
    "DomainObservationV1",
    "ObservationReceipt",
    "OutcomeClass",
    "OutcomeKind",
    "ReconciliationResult",
    "ReconcileRegistrarDomainV1",
    "RegisterDomain",
    "RegisterDomainV1",
    "RecordRegistrarOutcome",
    "RegistrarAcknowledgementV1",
    "RegistrarCapabilityV1",
    "ReleaseDomain",
    "RequestTransferDomain",
    "RenewDomain",
    "RenewDomainV1",
    "SetDomainIntent",
    "TransferDirection",
    "TransferDomainV1",
    "UpdateDomainContactsV1",
    "UpdateDomainNameserversV1",
    "canonical_dns_owner",
    "canonical_domain_name",
    "fingerprint",
    "release_content_digest",
    "transfer_out_content_digest",
]
