"""Provider-neutral hosting commands, observations and capability V1."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from dotmac_hosting.vocabulary import active_hosting_vocabulary

HOSTING_ACCOUNT_CAPABILITY = "hosting.account.v1"
HOSTING_ACCOUNT_OPERATIONS = frozenset(
    {"provision", "package", "suspension", "termination", "observation", "reconcile"}
)


class ContractError(ValueError):
    """A malformed hosting contract value."""


class HostingLifecycleState(StrEnum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENSION_REQUESTED = "suspension_requested"
    SUSPENDED = "suspended"
    RESTORATION_REQUESTED = "restoration_requested"
    TERMINATING = "terminating"
    TERMINATED = "terminated"


class HostingCommandKind(StrEnum):
    PROVISION = "provision"
    PACKAGE = "package"
    SUSPENSION = "suspension"
    TERMINATION = "termination"
    RECONCILE = "reconcile"
    RETENTION_HOLD = "retention_hold"


class SuspensionAction(StrEnum):
    SUSPEND = "suspend"
    RESTORE = "restore"


class OutcomeKind(StrEnum):
    APPLIED = "applied"
    DEFERRED = "deferred"
    SUPERSEDED = "superseded"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    REFUSED = "refused"


class OutcomeClass(StrEnum):
    SUCCEEDED = "succeeded"
    RETRYABLE = "retryable"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    TERMINAL = "terminal"


class ApprovalObservationState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ConsequenceDisposition(StrEnum):
    APPLIED = "applied"
    DEFERRED = "deferred"
    SUPERSEDED = "superseded"
    REFUSED = "refused"
    FAILED = "failed"


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
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ContractError(f"{name} must be a SHA-256 hex digest")
    return normalized


def _approval_digest(name: str, value: str) -> str:
    if not value.startswith("sha256:"):
        raise ContractError(f"{name} must use the sha256:<hex> approval grammar")
    normalized = value.removeprefix("sha256:")
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ContractError(f"{name} must use the sha256:<hex> approval grammar")
    return value


def _domain(value: str) -> str:
    supplied = value.strip().rstrip(".").lower()
    if not supplied or "://" in supplied or "/" in supplied or ":" in supplied:
        raise ContractError("primary_domain must be a domain name")
    try:
        canonical = supplied.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ContractError("primary_domain is not valid IDNA") from exc
    labels = canonical.split(".")
    if len(labels) < 2 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or any(not (character.isalnum() or character == "-") for character in label)
        for label in labels
    ):
        raise ContractError("primary_domain is not a valid domain name")
    return canonical


def fingerprint(payload: object) -> str:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    elif is_dataclass(payload) and not isinstance(payload, type):
        payload = asdict(payload)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class HostingAccountIdentityV1:
    """Provider-facing immutable account/contact snapshot; never a secret."""

    account_label: str
    administrative_email: str
    country_code: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "account_label", _required("account_label", self.account_label, 160)
        )
        email = _required("administrative_email", self.administrative_email, 254).lower()
        local, separator, domain = email.rpartition("@")
        if not separator or not local or "." not in domain:
            raise ContractError("administrative_email must be a valid email address")
        object.__setattr__(self, "administrative_email", email)
        country = self.country_code.strip().upper()
        if len(country) != 2 or not country.isalpha():
            raise ContractError("country_code must be an ISO alpha-2 code")
        object.__setattr__(self, "country_code", country)


@dataclass(frozen=True, slots=True)
class ProvisionHostingAccountV1:
    operation_reference: str
    package_ref: str
    primary_domain: str
    account_identity: HostingAccountIdentityV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_reference", _required("operation_reference", self.operation_reference, 120))
        object.__setattr__(self, "package_ref", _required("package_ref", self.package_ref))
        object.__setattr__(self, "primary_domain", _domain(self.primary_domain))
        if not isinstance(self.account_identity, HostingAccountIdentityV1):
            raise ContractError("account_identity must be a closed V1 snapshot")


@dataclass(frozen=True, slots=True)
class ChangeHostingPackageV1:
    operation_reference: str
    account_ref: str
    target_package_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_reference", _required("operation_reference", self.operation_reference, 120))
        object.__setattr__(self, "account_ref", _required("account_ref", self.account_ref))
        object.__setattr__(self, "target_package_ref", _required("target_package_ref", self.target_package_ref))


@dataclass(frozen=True, slots=True)
class ChangeHostingSuspensionV1:
    operation_reference: str
    account_ref: str
    action: SuspensionAction
    reason_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_reference", _required("operation_reference", self.operation_reference, 120))
        object.__setattr__(self, "account_ref", _required("account_ref", self.account_ref))
        object.__setattr__(self, "reason_ref", _required("reason_ref", self.reason_ref))


@dataclass(frozen=True, slots=True)
class TerminateHostingAccountV1:
    operation_reference: str
    account_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_reference", _required("operation_reference", self.operation_reference, 120))
        object.__setattr__(self, "account_ref", _required("account_ref", self.account_ref))


@dataclass(frozen=True, slots=True)
class ReconcileHostingAccountV1:
    operation_reference: str
    account_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_reference", _required("operation_reference", self.operation_reference, 120))
        object.__setattr__(self, "account_ref", _required("account_ref", self.account_ref))


@dataclass(frozen=True, slots=True)
class ObserveHostingAccountV1:
    operation_reference: str
    account_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_reference",
            _required("operation_reference", self.operation_reference, 120),
        )
        object.__setattr__(
            self, "account_ref", _required("account_ref", self.account_ref)
        )


@dataclass(frozen=True, slots=True)
class HostingAcknowledgementV1:
    operation_reference: str
    accepted_at: datetime
    provider_account_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_reference", _required("operation_reference", self.operation_reference, 120))
        _aware("accepted_at", self.accepted_at)
        if self.provider_account_ref is not None:
            object.__setattr__(self, "provider_account_ref", _required("provider_account_ref", self.provider_account_ref))


@dataclass(frozen=True, slots=True)
class HostingResourceFactV1:
    resource_kind: str
    quantity: Decimal
    unit: str
    period_start: datetime | None = None
    period_end: datetime | None = None

    def __post_init__(self) -> None:
        active_hosting_vocabulary().require_resource_kind(self.resource_kind)
        try:
            quantity = Decimal(str(self.quantity))
        except InvalidOperation as exc:
            raise ContractError("quantity must be an exact decimal") from exc
        if not quantity.is_finite() or quantity < 0:
            raise ContractError("quantity must be finite and non-negative")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "unit", _required("unit", self.unit, 48))
        _aware("period_start", self.period_start)
        _aware("period_end", self.period_end)
        if self.period_start is not None and self.period_end is not None and self.period_end < self.period_start:
            raise ContractError("period_end cannot precede period_start")


@dataclass(frozen=True, slots=True)
class HostingObservationV1:
    provider_account_ref: str
    provider_event_id: str
    capability_binding_ref: str
    observation_kind: str
    provider_statuses: tuple[str, ...]
    observed_at: datetime
    operation_reference: str | None = None
    observed_package_ref: str | None = None
    resources: tuple[HostingResourceFactV1, ...] = ()
    source_mode: str = "ingress"

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_account_ref", _required("provider_account_ref", self.provider_account_ref))
        object.__setattr__(self, "provider_event_id", _required("provider_event_id", self.provider_event_id))
        object.__setattr__(self, "capability_binding_ref", _required("capability_binding_ref", self.capability_binding_ref))
        if self.operation_reference is not None:
            object.__setattr__(
                self,
                "operation_reference",
                _required("operation_reference", self.operation_reference, 120),
            )
        active_hosting_vocabulary().require_observation_kind(self.observation_kind)
        statuses = tuple(_required("provider_status", value, 255) for value in self.provider_statuses)
        if not statuses:
            raise ContractError("provider_statuses cannot be empty")
        object.__setattr__(self, "provider_statuses", statuses)
        if self.observed_package_ref is not None:
            object.__setattr__(self, "observed_package_ref", _required("observed_package_ref", self.observed_package_ref))
        if self.source_mode not in {"ingress", "poll"}:
            raise ContractError("source_mode must be ingress or poll")
        _aware("observed_at", self.observed_at)
        identities = tuple((fact.resource_kind, fact.unit, fact.period_start, fact.period_end) for fact in self.resources)
        if len(identities) != len(set(identities)):
            raise ContractError("resource facts must be unique by kind, unit and period")


@dataclass(frozen=True, slots=True)
class HostingAllowance:
    resource_kind: str
    quantity: Decimal
    unit: str

    def __post_init__(self) -> None:
        active_hosting_vocabulary().require_resource_kind(self.resource_kind)
        try:
            quantity = Decimal(str(self.quantity))
        except InvalidOperation as exc:
            raise ContractError("allowance quantity must be an exact decimal") from exc
        if not quantity.is_finite() or quantity < 0:
            raise ContractError("allowance quantity must be finite and non-negative")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "unit", _required("unit", self.unit, 48))


@dataclass(frozen=True, slots=True)
class HostingChangeRules:
    upgrade_allowed: bool
    downgrade_allowed: bool
    downgrade_requires_review: bool
    same_level_allowed: bool

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, bool)
            for value in (
                self.upgrade_allowed,
                self.downgrade_allowed,
                self.downgrade_requires_review,
                self.same_level_allowed,
            )
        ):
            raise ContractError("hosting change rules must be explicit booleans")


@dataclass(frozen=True, slots=True)
class PublishHostingSpecificationVersion:
    specification_code: str
    package_ref: str
    package_rank: int
    allowances: tuple[HostingAllowance, ...]
    included_artifacts: tuple[str, ...]
    capability_codes: tuple[str, ...]
    change_rules: HostingChangeRules
    published_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "specification_code", _required("specification_code", self.specification_code, 120))
        object.__setattr__(self, "package_ref", _required("package_ref", self.package_ref))
        if not isinstance(self.package_rank, int) or isinstance(self.package_rank, bool) or self.package_rank < 0:
            raise ContractError("package_rank must be a non-negative integer")
        if not all(isinstance(item, HostingAllowance) for item in self.allowances):
            raise ContractError("allowances must contain only HostingAllowance facts")
        if not isinstance(self.change_rules, HostingChangeRules):
            raise ContractError("change_rules must be a closed HostingChangeRules value")
        identities = tuple((item.resource_kind, item.unit) for item in self.allowances)
        if not identities or len(identities) != len(set(identities)):
            raise ContractError("allowances must be non-empty and unique by kind/unit")
        object.__setattr__(self, "included_artifacts", tuple(_required("included_artifact", value, 120) for value in self.included_artifacts))
        object.__setattr__(self, "capability_codes", tuple(_required("capability_code", value, 120) for value in self.capability_codes))
        if len(self.included_artifacts) != len(set(self.included_artifacts)):
            raise ContractError("included_artifacts must be unique")
        if len(self.capability_codes) != len(set(self.capability_codes)):
            raise ContractError("capability_codes must be unique")
        _aware("published_at", self.published_at)


@dataclass(frozen=True, slots=True)
class ProvisionHostingService:
    customer_ref: str
    order_line_ref: str
    offer_version_ref: str
    specification_code: str
    specification_version: int
    primary_domain: str
    account_identity: HostingAccountIdentityV1
    requested_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "customer_ref", _required("customer_ref", self.customer_ref))
        object.__setattr__(self, "order_line_ref", _required("order_line_ref", self.order_line_ref))
        object.__setattr__(self, "offer_version_ref", _required("offer_version_ref", self.offer_version_ref))
        object.__setattr__(self, "specification_code", _required("specification_code", self.specification_code, 120))
        if self.specification_version <= 0:
            raise ContractError("specification_version must be positive")
        object.__setattr__(self, "primary_domain", _domain(self.primary_domain))
        if not isinstance(self.account_identity, HostingAccountIdentityV1):
            raise ContractError("account_identity must be a closed V1 snapshot")
        _aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class ChangeHostingPackage:
    hosting_service_id: UUID
    specification_code: str
    specification_version: int
    requested_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "specification_code", _required("specification_code", self.specification_code, 120))
        if self.specification_version <= 0:
            raise ContractError("specification_version must be positive")
        _aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class SuspensionRequest:
    hosting_service_id: UUID
    reason_code: str
    source_owner: str
    source_reference: str
    requested_at: datetime

    def __post_init__(self) -> None:
        active_hosting_vocabulary().require_suspension_reason(self.reason_code)
        object.__setattr__(self, "source_owner", _required("source_owner", self.source_owner, 120))
        object.__setattr__(self, "source_reference", _required("source_reference", self.source_reference))
        _aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class RestoreSuspensionRequest:
    hosting_service_id: UUID
    reason_code: str
    restorer_code: str
    requested_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "reason_code", _required("reason_code", self.reason_code, 120)
        )
        object.__setattr__(self, "restorer_code", _required("restorer_code", self.restorer_code, 120))
        _aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class RetentionHoldRequest:
    hosting_service_id: UUID
    hold_code: str
    source_owner: str
    source_reference: str
    reason_code: str
    requested_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "hold_code", _required("hold_code", self.hold_code, 120))
        object.__setattr__(self, "source_owner", _required("source_owner", self.source_owner, 120))
        object.__setattr__(self, "source_reference", _required("source_reference", self.source_reference))
        object.__setattr__(self, "reason_code", _required("reason_code", self.reason_code, 160))
        _aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class ClearRetentionHold:
    hosting_service_id: UUID
    hold_code: str
    source_owner: str
    source_reference: str
    reason_code: str
    requested_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "hold_code", _required("hold_code", self.hold_code, 120))
        object.__setattr__(self, "source_owner", _required("source_owner", self.source_owner, 120))
        object.__setattr__(self, "source_reference", _required("source_reference", self.source_reference))
        object.__setattr__(self, "reason_code", _required("reason_code", self.reason_code, 160))
        _aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class TerminationApprovalObservationV1:
    """Closed mirror of the independently owned approval event."""

    event_type: str
    request_id: UUID
    subject_type: str
    subject_id: str
    policy_code: str
    policy_version: int
    content_digest: str
    state: ApprovalObservationState

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", _required("event_type", self.event_type, 120))
        if not isinstance(self.request_id, UUID):
            raise ContractError("request_id must be a UUID")
        object.__setattr__(self, "subject_type", _required("subject_type", self.subject_type, 120))
        object.__setattr__(self, "subject_id", _required("subject_id", self.subject_id))
        object.__setattr__(self, "policy_code", _required("policy_code", self.policy_code))
        if self.policy_version <= 0:
            raise ContractError("policy_version must be positive")
        object.__setattr__(
            self,
            "content_digest",
            _approval_digest("content_digest", self.content_digest),
        )
        if not isinstance(self.state, ApprovalObservationState):
            raise ContractError("state must be an ApprovalObservationState")


@dataclass(frozen=True, slots=True)
class RequestTermination:
    hosting_service_id: UUID
    expected_version: int
    requested_at: datetime
    approval_request_id: UUID

    def __post_init__(self) -> None:
        if self.expected_version < 0:
            raise ContractError("expected_version must be non-negative")
        if not isinstance(self.approval_request_id, UUID):
            raise ContractError("approval_request_id must be a UUID")
        _aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class HostingOutcomeEvidenceV1:
    """Closed business evidence; transport diagnostics stay in Integrator."""

    provider_statuses: tuple[str, ...] = ()
    diagnostic_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        statuses = tuple(_required("provider_status", value) for value in self.provider_statuses)
        diagnostics = tuple(
            _required("diagnostic_code", value, 120) for value in self.diagnostic_codes
        )
        if len(statuses) != len(set(statuses)):
            raise ContractError("provider_statuses must be unique")
        if len(diagnostics) != len(set(diagnostics)):
            raise ContractError("diagnostic_codes must be unique")
        object.__setattr__(self, "provider_statuses", statuses)
        object.__setattr__(self, "diagnostic_codes", diagnostics)


@dataclass(frozen=True, slots=True)
class RecordHostingOutcome:
    hosting_command_id: UUID
    evidence_key: str
    outcome_kind: OutcomeKind
    outcome_class: OutcomeClass
    occurred_at: datetime
    provider_reference: str | None = None
    reason_code: str | None = None
    evidence: HostingOutcomeEvidenceV1 = field(
        default_factory=HostingOutcomeEvidenceV1
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_key", _required("evidence_key", self.evidence_key))
        if self.outcome_kind not in {OutcomeKind.ACKNOWLEDGED, OutcomeKind.FAILED}:
            raise ContractError(
                "inbound provider outcomes may only acknowledge or fail delivery; "
                "business application requires an independent observation"
            )
        if self.provider_reference is not None:
            object.__setattr__(self, "provider_reference", _required("provider_reference", self.provider_reference))
        if self.reason_code is not None:
            object.__setattr__(self, "reason_code", _required("reason_code", self.reason_code, 160))
        _aware("occurred_at", self.occurred_at)


@dataclass(frozen=True, slots=True)
class Actor:
    actor_type: str
    actor_id: str | None
    actor_label: str | None = None
    actor_party_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.actor_type not in {"system", "user", "api_key", "service"}:
            raise ContractError("actor_type is not a canonical audit actor kind")
        if self.actor_type != "system" and not (self.actor_id and self.actor_id.strip()):
            raise ContractError("every non-system actor requires actor_id")
        if self.actor_type == "user":
            if self.actor_party_id is None or self.actor_id != str(self.actor_party_id):
                raise ContractError("a Cloud user actor must identify the same Party")
        if self.actor_type in {"system", "service"} and self.actor_party_id is not None:
            raise ContractError(f"{self.actor_type} actors cannot carry Party authority")


@dataclass(frozen=True, slots=True)
class HostingCommandReceipt:
    hosting_service_id: UUID
    command_id: UUID
    command_kind: HostingCommandKind
    lifecycle_state: HostingLifecycleState
    replayed: bool


@dataclass(frozen=True, slots=True)
class SpecificationPublicationReceipt:
    specification_version_id: UUID
    specification_code: str
    assigned_version: int
    previous_version: int | None
    content_digest: str
    previous_content_digest: str | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class HostingObservationReceipt:
    observation_id: UUID
    duplicate: bool


@dataclass(frozen=True, slots=True)
class TerminationApprovalEvidenceReceipt:
    approval_evidence_id: UUID
    duplicate: bool


@dataclass(frozen=True, slots=True)
class HostingConsequenceOutcome:
    hosting_service_id: UUID
    command_id: UUID
    outcome_id: UUID
    disposition: ConsequenceDisposition
    lifecycle_state: HostingLifecycleState
    reason_code: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class HostingPackageChangeOutcome:
    hosting_service_id: UUID
    command_id: UUID
    outcome_id: UUID
    disposition: ConsequenceDisposition
    direction: str
    lifecycle_state: HostingLifecycleState
    reason_code: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class HostingRestorationOutcome:
    hosting_service_id: UUID
    command_id: UUID
    outcome_id: UUID
    disposition: ConsequenceDisposition
    lifecycle_state: HostingLifecycleState
    remaining_blockers: tuple[str, ...]
    reason_code: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class RetentionHoldOutcome:
    retention_hold_id: UUID | None
    command_id: UUID
    outcome_id: UUID
    disposition: ConsequenceDisposition
    reason_code: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class HostingDrift:
    desired_account_state: str
    observed_account_state: str | None
    desired_package_ref: str
    observed_package_ref: str | None
    account_state_disagrees: bool
    package_disagrees: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HostingReconciliationResult:
    hosting_service_id: UUID
    previous_state: HostingLifecycleState
    current_state: HostingLifecycleState
    changed: bool
    reason_code: str
    drift: HostingDrift


def termination_content_digest(
    tenant_id: UUID,
    hosting_service_id: UUID,
    expected_version: int,
    requested_at: datetime,
) -> str:
    return "sha256:" + fingerprint(
        {
            "tenant_id": str(tenant_id),
            "operation": "hosting.termination",
            "subject_type": "hosting_service",
            "subject_id": str(hosting_service_id),
            "expected_version": expected_version,
            "requested_at": requested_at.isoformat(),
        }
    )


class HostingAccountCapabilityV1(Protocol):
    capability_id: str
    supported_operations: frozenset[str]

    def provision(self, request: ProvisionHostingAccountV1) -> HostingAcknowledgementV1: ...

    def change_package(self, request: ChangeHostingPackageV1) -> HostingAcknowledgementV1: ...

    def change_suspension(self, request: ChangeHostingSuspensionV1) -> HostingAcknowledgementV1: ...

    def terminate(self, request: TerminateHostingAccountV1) -> HostingAcknowledgementV1: ...

    def observe(self, request: ObserveHostingAccountV1) -> HostingObservationV1: ...

    def reconcile(self, request: ReconcileHostingAccountV1) -> HostingObservationV1: ...


__all__ = [
    "HOSTING_ACCOUNT_CAPABILITY",
    "HOSTING_ACCOUNT_OPERATIONS",
    "Actor",
    "ApprovalObservationState",
    "ChangeHostingPackage",
    "ChangeHostingPackageV1",
    "ChangeHostingSuspensionV1",
    "ClearRetentionHold",
    "ConsequenceDisposition",
    "ContractError",
    "HostingAccountCapabilityV1",
    "HostingAccountIdentityV1",
    "HostingAcknowledgementV1",
    "HostingAllowance",
    "HostingChangeRules",
    "HostingCommandKind",
    "HostingCommandReceipt",
    "HostingConsequenceOutcome",
    "HostingDrift",
    "HostingLifecycleState",
    "HostingObservationReceipt",
    "HostingObservationV1",
    "HostingPackageChangeOutcome",
    "HostingOutcomeEvidenceV1",
    "HostingReconciliationResult",
    "HostingResourceFactV1",
    "HostingRestorationOutcome",
    "OutcomeClass",
    "OutcomeKind",
    "ObserveHostingAccountV1",
    "ProvisionHostingAccountV1",
    "ProvisionHostingService",
    "PublishHostingSpecificationVersion",
    "ReconcileHostingAccountV1",
    "RecordHostingOutcome",
    "RequestTermination",
    "RestoreSuspensionRequest",
    "RetentionHoldRequest",
    "RetentionHoldOutcome",
    "SuspensionAction",
    "SuspensionRequest",
    "SpecificationPublicationReceipt",
    "TerminateHostingAccountV1",
    "TerminationApprovalObservationV1",
    "TerminationApprovalEvidenceReceipt",
    "fingerprint",
    "termination_content_digest",
]
