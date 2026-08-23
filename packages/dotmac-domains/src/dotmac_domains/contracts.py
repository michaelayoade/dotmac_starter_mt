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
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
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
    REGISTRATION_FAILED = "registration_failed"
    ACTIVE = "active"
    RENEWAL_REQUESTED = "renewal_requested"
    TRANSFER_OUT_REQUESTED = "transfer_out_requested"
    EXPIRED = "expired"
    REDEMPTION = "redemption"
    RELEASED = "released"
    UNKNOWN = "unknown"


class DomainCommandKind(StrEnum):
    REGISTRATION = "registration"
    RENEWAL = "renewal"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_CANCEL = "transfer_cancel"
    CONTACTS = "contacts"
    NAMESERVERS = "nameservers"
    DNS_ZONE = "dns_zone"
    DNS_RECORDSET = "dns_recordset"
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
    elif is_dataclass(payload) and not isinstance(payload, type):
        payload = asdict(payload)
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


def _exact_fields(
    name: str,
    payload: Mapping[str, object],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    supplied = frozenset(payload)
    if supplied - required - optional or required - supplied:
        raise ContractError(
            f"{name} payload must contain exact fields "
            f"required={sorted(required)}, optional={sorted(optional)}"
        )


def _mapping(name: str, value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{name} must be an object")
    return value


def _string(name: str, value: object, *, limit: int = 255) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{name} must be a string")
    return _required(name, value, limit)


def _optional_string(name: str, value: object, *, limit: int = 255) -> str | None:
    if value is None:
        return None
    return _string(name, value, limit=limit)


def _datetime(name: str, value: object) -> datetime:
    if not isinstance(value, str):
        raise ContractError(f"{name} must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{name} must be an ISO 8601 string") from exc
    _aware(name, parsed)
    return parsed


@dataclass(frozen=True, slots=True)
class DomainPostalAddressV1:
    """Closed registrar-contact address snapshot; never a live customer row."""

    line_one: str
    city: str
    country_code: str
    line_two: str | None = None
    region: str | None = None
    postal_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "line_one", _required("line_one", self.line_one))
        object.__setattr__(self, "city", _required("city", self.city, 120))
        country = self.country_code.strip().upper()
        if len(country) != 2 or not country.isalpha():
            raise ContractError("country_code must be ISO 3166-1 alpha-2")
        object.__setattr__(self, "country_code", country)
        for field_name, limit in (
            ("line_two", 255),
            ("region", 120),
            ("postal_code", 32),
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self, field_name, _required(field_name, value, limit)
                )

    def to_payload(self) -> dict[str, object]:
        return {
            "line_one": self.line_one,
            "line_two": self.line_two,
            "city": self.city,
            "region": self.region,
            "postal_code": self.postal_code,
            "country_code": self.country_code,
        }

    @classmethod
    def from_payload(cls, value: object) -> DomainPostalAddressV1:
        payload = _mapping("address", value)
        _exact_fields(
            "address",
            payload,
            required=frozenset({"line_one", "city", "country_code"}),
            optional=frozenset({"line_two", "region", "postal_code"}),
        )
        return cls(
            line_one=_string("line_one", payload["line_one"]),
            line_two=_optional_string("line_two", payload.get("line_two")),
            city=_string("city", payload["city"], limit=120),
            region=_optional_string("region", payload.get("region"), limit=120),
            postal_code=_optional_string(
                "postal_code", payload.get("postal_code"), limit=32
            ),
            country_code=_string("country_code", payload["country_code"], limit=2),
        )


@dataclass(frozen=True, slots=True)
class DomainContactV1:
    """One provider-neutral contact role with no open metadata or secret field."""

    full_name: str
    email: str
    phone: str
    address: DomainPostalAddressV1
    organization: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.address, DomainPostalAddressV1):
            raise ContractError("address must be a DomainPostalAddressV1 snapshot")
        object.__setattr__(
            self, "full_name", _required("full_name", self.full_name, 160)
        )
        email = self.email.strip().lower()
        if len(email) > 254 or email.count("@") != 1 or email.startswith("@"):
            raise ContractError("email must be a valid provider contact address")
        object.__setattr__(self, "email", email)
        phone = self.phone.strip()
        if (
            not phone.startswith("+")
            or not phone[1:].isdigit()
            or not 8 <= len(phone[1:]) <= 15
        ):
            raise ContractError("phone must be E.164")
        object.__setattr__(self, "phone", phone)
        if self.organization is not None:
            object.__setattr__(
                self,
                "organization",
                _required("organization", self.organization, 160),
            )

    def to_payload(self) -> dict[str, object]:
        return {
            "full_name": self.full_name,
            "organization": self.organization,
            "email": self.email,
            "phone": self.phone,
            "address": self.address.to_payload(),
        }

    @classmethod
    def from_payload(cls, value: object) -> DomainContactV1:
        payload = _mapping("contact", value)
        _exact_fields(
            "contact",
            payload,
            required=frozenset({"full_name", "email", "phone", "address"}),
            optional=frozenset({"organization"}),
        )
        return cls(
            full_name=_string("full_name", payload["full_name"], limit=160),
            organization=_optional_string(
                "organization", payload.get("organization"), limit=160
            ),
            email=_string("email", payload["email"], limit=254),
            phone=_string("phone", payload["phone"], limit=16),
            address=DomainPostalAddressV1.from_payload(payload["address"]),
        )


@dataclass(frozen=True, slots=True)
class DomainContactSetV1:
    """Immutable desired registrar contacts plus their source provenance."""

    source_authority: str
    source_reference: str
    source_version: str
    registrant: DomainContactV1
    administrative: DomainContactV1
    technical: DomainContactV1
    billing: DomainContactV1

    def __post_init__(self) -> None:
        for role in ("registrant", "administrative", "technical", "billing"):
            if not isinstance(getattr(self, role), DomainContactV1):
                raise ContractError(f"{role} must be a DomainContactV1 snapshot")
        object.__setattr__(
            self,
            "source_authority",
            _required("source_authority", self.source_authority, 120),
        )
        object.__setattr__(
            self,
            "source_reference",
            _required("source_reference", self.source_reference),
        )
        object.__setattr__(
            self, "source_version", _required("source_version", self.source_version)
        )

    def provider_content_payload(self) -> dict[str, object]:
        """The registrar-visible values, excluding local source provenance."""

        return {
            "registrant": self.registrant.to_payload(),
            "administrative": self.administrative.to_payload(),
            "technical": self.technical.to_payload(),
            "billing": self.billing.to_payload(),
        }

    @property
    def contact_content_digest(self) -> str:
        return fingerprint(self.provider_content_payload())

    @property
    def provenance_digest(self) -> str:
        return fingerprint(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            "source_authority": self.source_authority,
            "source_reference": self.source_reference,
            "source_version": self.source_version,
            "registrant": self.registrant.to_payload(),
            "administrative": self.administrative.to_payload(),
            "technical": self.technical.to_payload(),
            "billing": self.billing.to_payload(),
        }

    @classmethod
    def from_payload(cls, value: object) -> DomainContactSetV1:
        payload = _mapping("contact_set", value)
        fields = frozenset(
            {
                "source_authority",
                "source_reference",
                "source_version",
                "registrant",
                "administrative",
                "technical",
                "billing",
            }
        )
        _exact_fields("contact_set", payload, required=fields)
        return cls(
            source_authority=_string(
                "source_authority", payload["source_authority"], limit=120
            ),
            source_reference=_string(
                "source_reference", payload["source_reference"]
            ),
            source_version=_string("source_version", payload["source_version"]),
            registrant=DomainContactV1.from_payload(payload["registrant"]),
            administrative=DomainContactV1.from_payload(payload["administrative"]),
            technical=DomainContactV1.from_payload(payload["technical"]),
            billing=DomainContactV1.from_payload(payload["billing"]),
        )


@dataclass(frozen=True, slots=True)
class RegisterDomainV1:
    operation_reference: str
    name: str
    term_months: int
    contact_set: DomainContactSetV1
    nameservers: tuple[str, ...]
    privacy_requested: bool

    def __post_init__(self) -> None:
        if not isinstance(self.contact_set, DomainContactSetV1):
            raise ContractError("contact_set must be a DomainContactSetV1 snapshot")
        object.__setattr__(
            self,
            "operation_reference",
            _required("operation_reference", self.operation_reference, 120),
        )
        object.__setattr__(self, "name", canonical_domain_name(self.name))
        if self.term_months <= 0 or self.term_months > 120:
            raise ContractError("term_months must be between 1 and 120")
        object.__setattr__(self, "nameservers", _nameservers(self.nameservers))

    def to_payload(self) -> dict[str, object]:
        return {
            "operation_reference": self.operation_reference,
            "name": self.name,
            "term_months": self.term_months,
            "contact_set": self.contact_set.to_payload(),
            "nameservers": list(self.nameservers),
            "privacy_requested": self.privacy_requested,
        }

    @classmethod
    def from_payload(cls, value: object) -> RegisterDomainV1:
        payload = _mapping("registration", value)
        fields = frozenset(
            {
                "operation_reference",
                "name",
                "term_months",
                "contact_set",
                "nameservers",
                "privacy_requested",
            }
        )
        _exact_fields("registration", payload, required=fields)
        nameservers = payload["nameservers"]
        if not isinstance(nameservers, list | tuple) or not all(
            isinstance(item, str) for item in nameservers
        ):
            raise ContractError("nameservers must be a list of names")
        term_months = payload["term_months"]
        if not isinstance(term_months, int) or isinstance(term_months, bool):
            raise ContractError("term_months must be an integer")
        privacy_requested = payload["privacy_requested"]
        if not isinstance(privacy_requested, bool):
            raise ContractError("privacy_requested must be a boolean")
        return cls(
            operation_reference=_string(
                "operation_reference", payload["operation_reference"], limit=120
            ),
            name=_string("name", payload["name"]),
            term_months=term_months,
            contact_set=DomainContactSetV1.from_payload(payload["contact_set"]),
            nameservers=tuple(nameservers),
            privacy_requested=privacy_requested,
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

    def to_payload(self) -> dict[str, object]:
        return {
            "operation_reference": self.operation_reference,
            "name": self.name,
            "term_months": self.term_months,
            "observed_expires_at": self.observed_expires_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, value: object) -> RenewDomainV1:
        payload = _mapping("renewal", value)
        fields = frozenset(
            {"operation_reference", "name", "term_months", "observed_expires_at"}
        )
        _exact_fields("renewal", payload, required=fields)
        term_months = payload["term_months"]
        if not isinstance(term_months, int) or isinstance(term_months, bool):
            raise ContractError("term_months must be an integer")
        return cls(
            operation_reference=_string(
                "operation_reference", payload["operation_reference"], limit=120
            ),
            name=_string("name", payload["name"]),
            term_months=term_months,
            observed_expires_at=_datetime(
                "observed_expires_at", payload["observed_expires_at"]
            ),
        )


@dataclass(frozen=True, slots=True)
class TransferDomainV1:
    operation_reference: str
    name: str
    direction: TransferDirection

    def __post_init__(self) -> None:
        if not isinstance(self.direction, TransferDirection):
            raise ContractError("direction must be a TransferDirection")
        object.__setattr__(
            self,
            "operation_reference",
            _required("operation_reference", self.operation_reference, 120),
        )
        object.__setattr__(self, "name", canonical_domain_name(self.name))

    def to_payload(self) -> dict[str, object]:
        return {
            "operation_reference": self.operation_reference,
            "name": self.name,
            "direction": self.direction.value,
        }

    @classmethod
    def from_payload(cls, value: object) -> TransferDomainV1:
        payload = _mapping("transfer", value)
        fields = frozenset({"operation_reference", "name", "direction"})
        _exact_fields("transfer", payload, required=fields)
        direction = _string("direction", payload["direction"], limit=32)
        try:
            typed_direction = TransferDirection(direction)
        except ValueError as exc:
            raise ContractError(
                f"unsupported transfer direction {direction!r}"
            ) from exc
        return cls(
            operation_reference=_string(
                "operation_reference", payload["operation_reference"], limit=120
            ),
            name=_string("name", payload["name"]),
            direction=typed_direction,
        )


@dataclass(frozen=True, slots=True)
class UpdateDomainContactsV1:
    operation_reference: str
    name: str
    contact_set: DomainContactSetV1

    def __post_init__(self) -> None:
        if not isinstance(self.contact_set, DomainContactSetV1):
            raise ContractError("contact_set must be a DomainContactSetV1 snapshot")
        object.__setattr__(
            self,
            "operation_reference",
            _required("operation_reference", self.operation_reference, 120),
        )
        object.__setattr__(self, "name", canonical_domain_name(self.name))

    def to_payload(self) -> dict[str, object]:
        return {
            "operation_reference": self.operation_reference,
            "name": self.name,
            "contact_set": self.contact_set.to_payload(),
        }

    @classmethod
    def from_payload(cls, value: object) -> UpdateDomainContactsV1:
        payload = _mapping("contacts", value)
        fields = frozenset({"operation_reference", "name", "contact_set"})
        _exact_fields("contacts", payload, required=fields)
        return cls(
            operation_reference=_string(
                "operation_reference", payload["operation_reference"], limit=120
            ),
            name=_string("name", payload["name"]),
            contact_set=DomainContactSetV1.from_payload(payload["contact_set"]),
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

    def to_payload(self) -> dict[str, object]:
        return {
            "operation_reference": self.operation_reference,
            "name": self.name,
            "nameservers": list(self.nameservers),
        }

    @classmethod
    def from_payload(cls, value: object) -> UpdateDomainNameserversV1:
        payload = _mapping("nameservers", value)
        fields = frozenset({"operation_reference", "name", "nameservers"})
        _exact_fields("nameservers", payload, required=fields)
        nameservers = payload["nameservers"]
        if not isinstance(nameservers, list | tuple) or not all(
            isinstance(item, str) for item in nameservers
        ):
            raise ContractError("nameservers must be a list of names")
        return cls(
            operation_reference=_string(
                "operation_reference", payload["operation_reference"], limit=120
            ),
            name=_string("name", payload["name"]),
            nameservers=tuple(nameservers),
        )


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

    def to_payload(self) -> dict[str, object]:
        return {
            "owner": self.owner,
            "record_type": self.record_type,
            "ttl": self.ttl,
            "values": list(self.values),
        }

    @classmethod
    def from_payload(cls, value: object) -> DNSRecordSetV1:
        payload = _mapping("recordset", value)
        fields = frozenset({"owner", "record_type", "ttl", "values"})
        _exact_fields("recordset", payload, required=fields)
        ttl = payload["ttl"]
        values = payload["values"]
        if not isinstance(ttl, int) or isinstance(ttl, bool):
            raise ContractError("ttl must be an integer")
        if not isinstance(values, list | tuple) or not all(
            isinstance(item, str) for item in values
        ):
            raise ContractError("values must be a list of strings")
        return cls(
            owner=_string("owner", payload["owner"]),
            record_type=_string("record_type", payload["record_type"], limit=16),
            ttl=ttl,
            values=tuple(values),
        )


def canonical_recordsets_digest(recordsets: tuple[DNSRecordSetV1, ...]) -> str:
    """Digest provider-visible DNS state independent of recordset ordering."""

    canonical = sorted(
        (
            {
                **recordset.to_payload(),
                "values": sorted(recordset.values),
            }
            for recordset in recordsets
        ),
        key=lambda item: (str(item["owner"]), str(item["record_type"])),
    )
    return fingerprint(canonical)


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

    def to_payload(self) -> dict[str, object]:
        return {
            "operation_reference": self.operation_reference,
            "zone_name": self.zone_name,
            "nameservers": list(self.nameservers),
        }

    @classmethod
    def from_payload(cls, value: object) -> ConfigureDNSZoneV1:
        payload = _mapping("zone", value)
        fields = frozenset({"operation_reference", "zone_name", "nameservers"})
        _exact_fields("zone", payload, required=fields)
        nameservers = payload["nameservers"]
        if not isinstance(nameservers, list | tuple) or not all(
            isinstance(item, str) for item in nameservers
        ):
            raise ContractError("nameservers must be a list of names")
        return cls(
            operation_reference=_string(
                "operation_reference", payload["operation_reference"], limit=120
            ),
            zone_name=_string("zone_name", payload["zone_name"]),
            nameservers=tuple(nameservers),
        )


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

    def to_payload(self) -> dict[str, object]:
        return {
            "operation_reference": self.operation_reference,
            "zone_name": self.zone_name,
            "recordsets": [recordset.to_payload() for recordset in self.recordsets],
        }

    @classmethod
    def from_payload(cls, value: object) -> ApplyDNSRecordSetsV1:
        payload = _mapping("recordsets", value)
        fields = frozenset({"operation_reference", "zone_name", "recordsets"})
        _exact_fields("recordsets", payload, required=fields)
        recordsets = payload["recordsets"]
        if not isinstance(recordsets, list | tuple):
            raise ContractError("recordsets must be a list")
        return cls(
            operation_reference=_string(
                "operation_reference", payload["operation_reference"], limit=120
            ),
            zone_name=_string("zone_name", payload["zone_name"]),
            recordsets=tuple(
                DNSRecordSetV1.from_payload(recordset) for recordset in recordsets
            ),
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
    recordsets: tuple[DNSRecordSetV1, ...] = ()
    source_mode: str = "ingress"

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
        identities = tuple(
            (record.owner, record.record_type) for record in self.recordsets
        )
        if len(identities) != len(set(identities)):
            raise ContractError("DNS recordsets must be unique by owner/type")
        if self.source_mode not in {"ingress", "poll"}:
            raise ContractError("source_mode must be ingress or poll")
        _aware("observed_at", self.observed_at)

    @property
    def recordsets_digest(self) -> str:
        return canonical_recordsets_digest(self.recordsets)


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
    contact_set: DomainContactSetV1
    nameservers: tuple[str, ...]
    privacy_requested: bool
    commercial_renewal_at: datetime
    requested_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.contact_set, DomainContactSetV1):
            raise ContractError("contact_set must be a DomainContactSetV1 snapshot")
        object.__setattr__(self, "name", canonical_domain_name(self.name))
        object.__setattr__(
            self, "order_line_ref", _required("order_line_ref", self.order_line_ref)
        )
        object.__setattr__(
            self,
            "offer_version_ref",
            _required("offer_version_ref", self.offer_version_ref),
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
    registrar_observation_id: UUID
    commercial_renewal_at: datetime
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
        _aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class DomainContactsIntent:
    contact_set: DomainContactSetV1

    def __post_init__(self) -> None:
        if not isinstance(self.contact_set, DomainContactSetV1):
            raise ContractError("contact_set must be a DomainContactSetV1 snapshot")


@dataclass(frozen=True, slots=True)
class DomainNameserversIntent:
    nameservers: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "nameservers", _nameservers(self.nameservers))


@dataclass(frozen=True, slots=True)
class DomainDNSZoneIntent:
    nameservers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "nameservers", _nameservers(self.nameservers))


@dataclass(frozen=True, slots=True)
class DomainDNSRecordsetsIntent:
    recordsets: tuple[DNSRecordSetV1, ...]

    def __post_init__(self) -> None:
        identities = tuple(
            (record.owner, record.record_type) for record in self.recordsets
        )
        if not identities or len(identities) != len(set(identities)):
            raise ContractError(
                "DNS recordsets must be non-empty and unique by owner/type"
            )


DomainIntentValue = (
    DomainContactsIntent
    | DomainNameserversIntent
    | DomainDNSZoneIntent
    | DomainDNSRecordsetsIntent
)


@dataclass(frozen=True, slots=True)
class SetDomainIntent:
    domain_service_id: UUID
    intent: DomainIntentValue
    requested_at: datetime

    def __post_init__(self) -> None:
        _aware("requested_at", self.requested_at)

    @property
    def intent_kind(self) -> str:
        if isinstance(self.intent, DomainContactsIntent):
            return "contacts"
        if isinstance(self.intent, DomainNameserversIntent):
            return "nameservers"
        if isinstance(self.intent, DomainDNSZoneIntent):
            return "dns_zone"
        return "dns_recordset"

    @property
    def content(self) -> dict[str, object]:
        if isinstance(self.intent, DomainContactsIntent):
            return {
                "contact_set": self.intent.contact_set.to_payload(),
                "contact_content_digest": (
                    self.intent.contact_set.contact_content_digest
                ),
                "provenance_digest": self.intent.contact_set.provenance_digest,
            }
        if isinstance(self.intent, DomainNameserversIntent | DomainDNSZoneIntent):
            return {"nameservers": list(self.intent.nameservers)}
        return {
            "recordsets": [item.to_payload() for item in self.intent.recordsets],
            "recordsets_digest": canonical_recordsets_digest(
                self.intent.recordsets
            ),
        }


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
    approval: ApprovalReceipt | None = None
    expected_version: int | None = None

    def __post_init__(self) -> None:
        _aware("requested_at", self.requested_at)
        if not isinstance(self.direction, TransferDirection):
            raise ContractError("direction must be approve_out or cancel")
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

    def __post_init__(self) -> None:
        if self.actor_type not in {"system", "user", "api_key", "service"}:
            raise ContractError("actor_type is not a canonical audit actor kind")
        if self.actor_type != "system" and not (
            self.actor_id is not None and self.actor_id.strip()
        ):
            raise ContractError("every non-system actor requires actor_id")
        if self.actor_type == "user":
            if self.actor_party_id is None:
                raise ContractError("a Cloud user actor requires actor_party_id")
            if self.actor_id != str(self.actor_party_id):
                raise ContractError("a Cloud user actor must identify the same Party")
        if self.actor_type in {"system", "service"} and self.actor_party_id is not None:
            raise ContractError(
                f"{self.actor_type} actors cannot carry Party authority"
            )


@dataclass(frozen=True, slots=True)
class DomainDrift:
    commercial_renewal_at: datetime | None
    registrar_expires_at: datetime | None
    expiry_disagrees: bool
    nameservers_disagree: bool
    contacts_disagree: bool
    dns_nameservers_disagree: bool
    dns_recordsets_disagree: bool
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
    dns_observation_id: UUID | None
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


def transfer_out_content_digest(name: str, command: RequestTransferDomain) -> str:
    if command.direction is not TransferDirection.APPROVE_OUT:
        raise ContractError("only a transfer-out request has approval content")
    payload = {
        "operation": "transfer_out",
        "domain_service_id": str(command.domain_service_id),
        "name": canonical_domain_name(name),
        "expected_version": command.expected_version,
        "requested_at": command.requested_at.isoformat(),
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
    "DomainContactSetV1",
    "DomainContactV1",
    "DomainContactsIntent",
    "DomainDNSRecordsetsIntent",
    "DomainDNSZoneIntent",
    "DomainDrift",
    "DomainIntentValue",
    "DomainLifecycleState",
    "DomainNameserversIntent",
    "DomainObservationV1",
    "DomainPostalAddressV1",
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
    "canonical_recordsets_digest",
    "fingerprint",
    "transfer_out_content_digest",
]
