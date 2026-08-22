"""Provider-neutral inter-application observation contract and receiver seam."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

_CAPABILITY = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\.v[1-9]\d*")
_APPLICATION = re.compile(r"[a-z][a-z0-9_]{1,119}")
_TEXT_LIMIT = 240


class SyncContractError(RuntimeError):
    """An inter-application contract cannot be used safely."""


class DuplicateContract(SyncContractError):
    """Two declarations claim one capability."""


class UnknownContract(SyncContractError):
    """The destination does not own the requested capability."""


class PeerMismatch(SyncContractError):
    """Authenticated peer identity differs from the envelope source."""


class EnvelopeInvalid(SyncContractError):
    """The wire envelope or its destination-owned payload is invalid."""


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _TEXT_LIMIT:
        raise EnvelopeInvalid(f"{label} must be a non-empty bounded string")
    return value


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class SyncContract:
    """One capability declared by its destination application."""

    capability_id: str
    owner_application: str
    summary: str
    payload_schema: Mapping[str, object]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.capability_id, str)
            or _CAPABILITY.fullmatch(self.capability_id) is None
        ):
            raise SyncContractError("capability_id must end in an explicit vN")
        if (
            not isinstance(self.owner_application, str)
            or _APPLICATION.fullmatch(self.owner_application) is None
        ):
            raise SyncContractError("owner_application is invalid")
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise SyncContractError("contract summary is required")
        if not isinstance(self.payload_schema, Mapping):
            raise SyncContractError("payload_schema must be an object")
        try:
            Draft202012Validator.check_schema(_plain(self.payload_schema))
        except SchemaError:
            raise SyncContractError("payload_schema is not valid JSON Schema") from None
        object.__setattr__(self, "payload_schema", _freeze(self.payload_schema))

    @property
    def version(self) -> int:
        return int(self.capability_id.rsplit(".v", 1)[1])


class SyncContractRegistry:
    """Closed declarations for one composed destination runtime."""

    def __init__(self, contracts: Iterable[SyncContract]) -> None:
        indexed: dict[str, SyncContract] = {}
        for contract in contracts:
            if contract.capability_id in indexed:
                raise DuplicateContract(
                    f"capability {contract.capability_id!r} is declared twice"
                )
            indexed[contract.capability_id] = contract
        self._contracts = MappingProxyType(indexed)

    def require(self, capability_id: str, *, owner: str) -> SyncContract:
        contract = self._contracts.get(capability_id)
        if contract is None or contract.owner_application != owner:
            raise UnknownContract(
                f"{owner!r} does not declare capability {capability_id!r}"
            )
        return contract


@dataclass(frozen=True, slots=True, repr=False)
class AuthenticatedPeer:
    """Identity already authenticated by the product's transport adapter."""

    application: str
    transport_subject: str | None = None

    def __post_init__(self) -> None:
        if _APPLICATION.fullmatch(self.application) is None:
            raise EnvelopeInvalid("peer application is invalid")


@dataclass(frozen=True, slots=True, repr=False)
class SyncEnvelope:
    """One observation emitted from a local durable outbox."""

    capability_id: str
    source_application: str
    source_event_id: str
    source_scope_kind: str
    source_scope_ref: str
    subject_ref: str
    occurred_at: str
    correlation_id: str
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.capability_id, str)
            or _CAPABILITY.fullmatch(self.capability_id) is None
        ):
            raise EnvelopeInvalid("capability_id must end in an explicit vN")
        if (
            not isinstance(self.source_application, str)
            or _APPLICATION.fullmatch(self.source_application) is None
        ):
            raise EnvelopeInvalid("source_application is invalid")
        for label in (
            "source_event_id",
            "source_scope_kind",
            "source_scope_ref",
            "subject_ref",
            "correlation_id",
        ):
            _required_text(getattr(self, label), label)
        occurred_at = _required_text(self.occurred_at, "occurred_at")
        try:
            parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            raise EnvelopeInvalid("occurred_at is not an ISO-8601 timestamp") from None
        if parsed.tzinfo is None:
            raise EnvelopeInvalid("occurred_at has no timezone")
        if not isinstance(self.payload, Mapping):
            raise EnvelopeInvalid("payload must be an object")
        object.__setattr__(self, "payload", _freeze(self.payload))

    @property
    def contract_version(self) -> int:
        return int(self.capability_id.rsplit(".v", 1)[1])


class SyncAcceptance(str, Enum):
    ACCEPTED = "accepted"
    ALREADY_APPLIED = "already_applied"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class SyncReceipt:
    acceptance: SyncAcceptance
    destination_ref: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if (
            self.acceptance
            in {
                SyncAcceptance.ACCEPTED,
                SyncAcceptance.ALREADY_APPLIED,
            }
            and not self.destination_ref
        ):
            raise EnvelopeInvalid("an applied receipt requires destination_ref")
        if self.acceptance is SyncAcceptance.REJECTED and not self.error_code:
            raise EnvelopeInvalid("a rejected receipt requires error_code")


@runtime_checkable
class SyncReceiver(Protocol):
    """Destination-owned atomic deduplication plus local resolver invocation."""

    def receive(
        self,
        *,
        envelope: SyncEnvelope,
        idempotency_key: str,
        fingerprint: str,
    ) -> SyncReceipt: ...


def _wire(envelope: SyncEnvelope) -> dict[str, object]:
    return {
        "capability_id": envelope.capability_id,
        "contract_version": envelope.contract_version,
        "source_application": envelope.source_application,
        "source_event_id": envelope.source_event_id,
        "source_scope": {
            "kind": envelope.source_scope_kind,
            "ref": envelope.source_scope_ref,
        },
        "subject_ref": envelope.subject_ref,
        "occurred_at": envelope.occurred_at,
        "correlation_id": envelope.correlation_id,
        "payload": _plain(envelope.payload),
    }


def encode_envelope(envelope: SyncEnvelope) -> bytes:
    return json.dumps(_wire(envelope), sort_keys=True, separators=(",", ":")).encode()


def _decode(raw_body: bytes) -> SyncEnvelope:
    try:
        value = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EnvelopeInvalid("request body is not JSON") from None
    if not isinstance(value, dict) or set(value) != {
        "capability_id",
        "contract_version",
        "source_application",
        "source_event_id",
        "source_scope",
        "subject_ref",
        "occurred_at",
        "correlation_id",
        "payload",
    }:
        raise EnvelopeInvalid("request envelope has an invalid shape")
    scope = value["source_scope"]
    payload = value["payload"]
    if not isinstance(scope, dict) or set(scope) != {"kind", "ref"}:
        raise EnvelopeInvalid("source_scope has an invalid shape")
    if not isinstance(payload, dict):
        raise EnvelopeInvalid("payload is not an object")
    envelope = SyncEnvelope(
        capability_id=value["capability_id"],
        source_application=value["source_application"],
        source_event_id=value["source_event_id"],
        source_scope_kind=scope["kind"],
        source_scope_ref=scope["ref"],
        subject_ref=value["subject_ref"],
        occurred_at=value["occurred_at"],
        correlation_id=value["correlation_id"],
        payload=payload,
    )
    if value["contract_version"] != envelope.contract_version:
        raise EnvelopeInvalid("contract_version disagrees with capability_id")
    return envelope


def idempotency_key_for(envelope: SyncEnvelope) -> str:
    material = "|".join(
        (
            envelope.source_application,
            envelope.source_event_id,
            envelope.capability_id,
        )
    )
    return f"sync:{hashlib.sha256(material.encode()).hexdigest()}"


def fingerprint_for(envelope: SyncEnvelope) -> str:
    return hashlib.sha256(encode_envelope(envelope)).hexdigest()


def deliver_authenticated(
    raw_body: bytes,
    *,
    peer: AuthenticatedPeer,
    expected_owner: str,
    registry: SyncContractRegistry,
    receiver: SyncReceiver,
) -> SyncReceipt:
    """Validate, then delegate one atomic dedupe+resolve operation."""

    envelope = _decode(raw_body)
    if envelope.source_application != peer.application:
        raise PeerMismatch("authenticated peer differs from claimed source")
    contract = registry.require(envelope.capability_id, owner=expected_owner)
    try:
        Draft202012Validator(_plain(contract.payload_schema)).validate(
            _plain(envelope.payload)
        )
    except ValidationError:
        raise EnvelopeInvalid(
            "payload does not satisfy the destination contract"
        ) from None
    receipt = receiver.receive(
        envelope=envelope,
        idempotency_key=idempotency_key_for(envelope),
        fingerprint=fingerprint_for(envelope),
    )
    if not isinstance(receipt, SyncReceipt):
        raise SyncContractError("receiver returned the wrong receipt type")
    return receipt
