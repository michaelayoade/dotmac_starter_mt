"""Receipt-to-product delivery — landing a recorded observation where it belongs.

The inbox RECORDS that a provider event arrived and was verified. It does not
deliver it. Without this module the control plane can acknowledge a customer's
WhatsApp message and then deliver it nowhere, which is the half that was missing.

## The defect this replaces

`execution.claim_receipt` takes a receipt by mutating an IN-MEMORY ORM object::

    receipt.state = "processing"
    receipt.attempt_count += 1

Two workers both read the row, both see `state == "verified"`, both assign in
Python, and both believe they hold it. The check and the write are two steps
with a window between them, so the claim decides nothing — the module cannot be
run with more than one worker, and a single-worker deployment is not one we can
ship.

`execution.claim_delivery` already has the answer for the outbox: a CONDITIONAL
UPDATE where ``rowcount == 1`` *is* the claim, because the database evaluated
the predicate and the loser sees 0. This module brings the inbox up to that
same discipline rather than inventing a second one.

## Extending the receipt lifecycle, NOT a parallel ledger

A delivery is a STATE OF THE RECEIPT, not a row in a new table. ADR-0014 gives
at-most-once exactly one owner, and the specific failure it records is a second
ledger: "did this land?" with two answers and no tiebreak. `InboxReceipt`
already carries `state`, `attempt_count` and `consequence_json` — the columns
this engine needs are additions to it.

## Three phases, in this order, and the order is the design

1. **Claim** — one transaction. A conditional UPDATE leases the receipt. The
   first dispatch freezes trusted routing and request identity; retries use that
   frozen contract and corroborate current authority. Ends. Commits.
2. **Call** — the product is contacted with **no session held**. A network call
   inside a transaction holds a row lock for the duration of someone else's
   outage: one slow product and the connection pool is gone, having done no
   work. This phase touches no database at all.
3. **Settle** — a second transaction, a conditional UPDATE guarded by the
   claim's own identity (receipt, attempt number, and a live lease).

Phase 3's guard is what makes phase 2 safe to be slow. A worker whose lease
expired mid-call has already been superseded; its settlement must change
nothing, and it learns so from ``rowcount == 0``. That is :class:`LostClaim`,
raised rather than logged, because a worker that silently fails to settle looks
exactly like one that succeeded.

## Why a retried timeout does not deliver twice

The idempotency key sent to the product is derived from the RECEIPT and its
DESTINATION — never from the attempt number (see :func:`idempotency_key_for`,
and the test that pins it). So attempt 2 presents the same key attempt 1 did:

* attempt 1 times out after the product committed;
* attempt 2 sends the identical key;
* the product recognises it and answers ``ALREADY_APPLIED``;
* the engine records success, and the consequence happened exactly once.

Had the key carried the attempt number, every retry would be a fresh consequence
and the retry curve would become a duplication machine. This is also why an
unclassified transport failure is RETRYABLE rather than reconciliation-bound:
the key is what makes retrying safe. :attr:`ProductAcceptance.INDETERMINATE`
exists for the case a connector knows the key will *not* protect it.

## The destination is resolved, never proposed

Every field the product is addressed by comes from :class:`TrustedDestination`,
which the first claim resolves and freezes from trusted durable state. No payload,
no header map and no provider-supplied name reaches that decision — see
:func:`build_product_request`, whose signature is the property: it accepts a
claim, and there is no parameter through which provider metadata could name an
application, a scope or a contract version.

## What this module deliberately does not do

It runs no effect through `dotmac_kernel.idempotency`. ADR-0014 § 7 puts
non-transactional effects — an external call that cannot join the transaction —
out of that contract's scope, and says not to rebuild a reservation on top of
it. Delivery is exactly such an effect, so at-most-once here is a LEASE on the
receipt plus an idempotency key the product honours, which is the mechanism
ADR-0014 points to rather than a competitor to it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from dotmac_integration.capability_registry import (
    CapabilityContract,
    CapabilityRegistry,
    CapabilityRegistryError,
)
from dotmac_integration.destination_binding import (
    DestinationBinding,
    DestinationBindingError,
    capability_contract_document,
    capability_contract_from_document,
    destination_binding_document,
    destination_binding_from_document,
    resolve_destination,
)
from dotmac_integration.execution import (
    LostClaim,
    ReceiptVerificationOrigin,
    payload_digest,
    receipt_verification_origin,
)
from dotmac_integration.models import (
    SCHEMA,
    CapabilityBinding,
    ConnectorInstallation,
    InboxReceipt,
)
from dotmac_integration.retry import Outcome, OutcomeStatus, next_state

__all__ = [
    "DeliveryError",
    "DeliveryPreparationRefused",
    "DeliverySnapshot",
    "DeliveryReport",
    "FingerprintConflict",
    "LostClaim",
    "DEFAULT_LEASE_SECONDS",
    "claim_statement",
    "settle_statement",
    "ProductAcceptance",
    "PRODUCT_OBSERVATION_SCHEMA_VERSION",
    "PRODUCT_OBSERVATION_ORIGIN_SCHEMA_VERSION",
    "ReceiptOriginEvidence",
    "resolve_receipt_origin_evidence",
    "ProductObservationSource",
    "ProductPortClient",
    "ProductOutcome",
    "ProductRequest",
    "ReceiptClaim",
    "ReceiptClaimStore",
    "ReceiptClaims",
    "TransportFailure",
    "TrustedDestination",
    "TrustedScope",
    "build_product_request",
    "deliver_receipt",
    "idempotency_key_for",
    "product_observation_document",
    "request_fingerprint_for",
    "resolve_product_observation_source",
    "require_stable_fingerprint",
]


# ── Errors ──────────────────────────────────────────────────────────────────


class DeliveryError(RuntimeError):
    """A receipt cannot be delivered the way the caller asked."""


class DeliveryPreparationRefused(DeliveryError):
    """A durable refusal before dispatch, carrying only a privacy-safe code."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class FingerprintConflict(DeliveryError):
    """The same delivery identity, carrying a DIFFERENT request.

    A replay is safe when the request is identical — that is what a replay
    means. A changed fingerprint under an unchanged identity is a conflict, and
    silently overwriting it would deliver content the recorded consequence does
    not describe. Mirrors `dotmac_kernel.idempotency`'s fingerprint column,
    which exists for the identical reason.
    """


class TransportFailure(DeliveryError):
    """The product could not be reached, or answered unusably.

    The ONLY exception :func:`deliver_receipt` converts into an outcome. A
    gateway raising anything else has a defect rather than a bad network, and
    the engine deliberately lets it propagate without settling: swallowing a
    `TypeError` as "unavailable" would retry a bug on an exponential curve and
    report it as a flaky provider. The lease then expires and another worker
    recovers the receipt, which is what leases are for.
    """

    def __init__(self, message: str, *, error_code: str = "transport_failure") -> None:
        super().__init__(message)
        self.error_code = error_code


# ── The trusted destination (Team 3's contract, structurally) ───────────────
#
# Named here as PROTOCOLS rather than imported, because
# `dotmac_integration.destination_binding` is not merged yet. Team 3's frozen
# `LocalScope` and `DestinationBinding` satisfy these structurally, so the
# import becomes a one-line change and no field name is guessed twice. The
# protocols also state exactly which fields delivery is entitled to read.


@runtime_checkable
class TrustedScope(Protocol):
    """The destination application's own name for the stream.

    Carried, never interpreted — this engine derives no behaviour from `kind`
    or `ref`, because a transport holding an opinion about the destination's
    internal structure is the coupling ADR-0024 removes.
    """

    @property
    def kind(self) -> str: ...

    @property
    def ref(self) -> str: ...


@runtime_checkable
class TrustedDestination(Protocol):
    """Where an observation lands, resolved from durable state.

    `destination_revision_id` is provenance: it names the row in
    `capability_destination_revisions` that was current when the claim was
    taken, so an incident can answer "what was this routed to on the 3rd?" from
    the route history rather than from a guess.

    Structural on purpose. `destination_binding.DestinationBinding` satisfies it
    without importing anything from here, which keeps the routing authority and
    the delivery engine independently testable.
    """

    @property
    def capability_binding_id(self) -> UUID: ...

    @property
    def capability_id(self) -> str: ...

    @property
    def application(self) -> str: ...

    @property
    def scope(self) -> TrustedScope: ...

    @property
    def contract_version(self) -> int: ...

    @property
    def destination_revision_id(self) -> UUID: ...


# ── Value objects ───────────────────────────────────────────────────────────


def _frozen(value: object) -> object:
    """Deep-freeze a JSON-shaped value.

    Shallow freezing is the bug worth avoiding: a frozen dataclass holding a
    plain `dict` is immutable only at the top level, and the nested payload —
    the part that actually crosses the boundary — stays writable. A caller
    could then mutate a request AFTER its fingerprint was computed, which is
    precisely the window the fingerprint exists to close.
    """
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _frozen(v) for k, v in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_frozen(v) for v in value)
    return value


def _json_value(value: object) -> object:
    """Return a JSON-serializable copy of a deeply frozen value."""
    if isinstance(value, Mapping):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(v) for v in value]
    return value


@dataclass(frozen=True, slots=True)
class ProductObservationSource:
    """Durable engine provenance for one provider observation.

    The installation is the control-plane identity operators bind to a local
    product owner. The connector key is corroborating transport provenance; a
    product must never use it to select the local business owner.
    """

    installation_id: UUID
    connector_key: str

    def __post_init__(self) -> None:
        if not self.connector_key or self.connector_key != self.connector_key.strip():
            raise DeliveryError(
                "connector_key must be a non-empty value without surrounding whitespace"
            )


@dataclass(frozen=True, slots=True)
class DeliverySnapshot:
    """The request's immutable routing contract, without a second payload copy."""

    source: ProductObservationSource
    destination: DestinationBinding
    capability: CapabilityContract

    def document(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "state": "frozen",
            "source": {
                "installation_id": str(self.source.installation_id),
                "connector_key": self.source.connector_key,
            },
            "destination": destination_binding_document(self.destination),
            "capability": {
                "capability_id": self.capability.capability_id,
                "application": self.capability.owner.application,
                "owner_module": self.capability.owner.module,
                "summary": self.capability.summary,
                "contract": capability_contract_document(self.capability),
            },
        }

    @classmethod
    def from_document(cls, value: object) -> DeliverySnapshot:
        try:
            if (
                not isinstance(value, dict)
                or set(value)
                != {"schema_version", "state", "source", "destination", "capability"}
                or type(value["schema_version"]) is not int
                or value["schema_version"] != 1
                or value["state"] != "frozen"
            ):
                raise ValueError("invalid dispatch snapshot")
            source = value["source"]
            if not isinstance(source, dict) or set(source) != {
                "installation_id",
                "connector_key",
            }:
                raise ValueError("invalid dispatch source")
            if not isinstance(source["installation_id"], str) or not isinstance(
                source["connector_key"], str
            ):
                raise ValueError("invalid dispatch source types")
            raw_capability = value["capability"]
            if not isinstance(raw_capability, dict) or set(raw_capability) != {
                "capability_id",
                "application",
                "owner_module",
                "summary",
                "contract",
            }:
                raise ValueError("invalid dispatch capability")
            for key in ("capability_id", "application", "owner_module", "summary"):
                item = raw_capability[key]
                if not isinstance(item, str) or not item or item.strip() != item:
                    raise ValueError("invalid dispatch capability field")
            contract = raw_capability["contract"]
            if not isinstance(contract, Mapping):
                raise ValueError("invalid dispatch capability contract")
            capability = capability_contract_from_document(
                contract,
                capability_id=raw_capability["capability_id"],
                application=raw_capability["application"],
                owner_module=raw_capability["owner_module"],
                summary=raw_capability["summary"],
            )
            result = cls(
                capability=capability,
                source=ProductObservationSource(
                    UUID(source["installation_id"]), source["connector_key"]
                ),
                destination=destination_binding_from_document(value["destination"]),
            )
            if result.document() != value:
                raise ValueError("noncanonical dispatch snapshot")
            destination = result.destination
            port = destination.product_port
            if (
                capability.capability_id != destination.capability_id
                or capability.contract_version != destination.contract_version
                or capability.owner.application != destination.application
                or (
                    port is not None
                    and (
                        port.owner_module != capability.owner.module
                        or port.capability_summary != capability.summary
                        or (
                            port.capability_contract is not None
                            and capability_contract_document(port.capability_contract)
                            != capability_contract_document(capability)
                        )
                    )
                )
            ):
                raise ValueError("dispatch capability differs from destination")
            return result
        except (ValueError, TypeError, KeyError, AttributeError, DeliveryError) as exc:
            raise DeliveryPreparationRefused("dispatch_snapshot_unavailable") from exc


_UNSTARTED_SNAPSHOT = {"schema_version": 1, "state": "unstarted"}


def resolve_product_observation_source(
    session: Any, *, capability_binding_id: UUID
) -> ProductObservationSource:
    """Resolve engine-owned source provenance from one durable binding.

    Both the leased delivery path and the read-only shadow path need the same
    answer. Keeping the join here prevents a composing assembly from growing a
    second persistence interpretation just because shadow deliberately claims
    nothing.
    """

    from sqlalchemy import select

    row = session.execute(
        select(
            ConnectorInstallation.id,
            ConnectorInstallation.connector_key,
        )
        .join(
            CapabilityBinding,
            CapabilityBinding.installation_id == ConnectorInstallation.id,
        )
        .where(CapabilityBinding.id == capability_binding_id)
    ).one_or_none()
    if row is None:
        raise DeliveryError(
            "the capability binding has no durable connector installation source"
        )
    return ProductObservationSource(
        installation_id=row.id,
        connector_key=row.connector_key,
    )


@dataclass(frozen=True, slots=True)
class ReceiptOriginEvidence:
    """Original retained receipt context, opaque to product business policy.

    This is not provider authenticity, merchant identity or financial acceptance.
    Unknown historical context has no value of this type and is never backfilled.
    """

    receipt_id: UUID
    payload_digest: str
    verification_origin: ReceiptVerificationOrigin

    def __post_init__(self) -> None:
        if not isinstance(self.receipt_id, UUID):
            raise DeliveryError("original receipt identity must be a UUID")
        if (
            not isinstance(self.payload_digest, str)
            or len(self.payload_digest) != 64
            or any(char not in "0123456789abcdef" for char in self.payload_digest)
        ):
            raise DeliveryError("original receipt digest must be lowercase SHA-256")
        if not isinstance(self.verification_origin, ReceiptVerificationOrigin):
            raise DeliveryError(
                "original receipt requires recorded verification context"
            )
        try:
            # Revalidate even frozen inputs: callers can bypass dataclass
            # construction or mutate with object.__setattr__ before projection.
            ReceiptVerificationOrigin.__post_init__(self.verification_origin)
        except (AttributeError, TypeError, ValueError) as exc:
            raise DeliveryError(
                "original receipt verification context is invalid"
            ) from exc


def resolve_receipt_origin_evidence(
    session: Any, *, receipt_id: UUID
) -> ReceiptOriginEvidence | None:
    """Read the retained original receipt only; current config is never evidence.

    Public read boundary for an authenticated assembly's query/shadow transport.
    Missing receipts refuse; retained pre-origin history explicitly returns None.
    The caller supplies authorization and its own transaction boundary.
    """
    from sqlalchemy import select

    # Entity reads may return an altered ORM identity-map value before the
    # immutable database guard has rejected its flush. Read persisted scalars,
    # without autoflush, so the public evidence boundary cannot attest that value.
    with session.no_autoflush:
        receipt = session.execute(
            select(
                InboxReceipt.id,
                InboxReceipt.installation_id,
                InboxReceipt.capability_binding_id,
                InboxReceipt.payload_digest,
                InboxReceipt.verification_connector_key,
                InboxReceipt.verification_manifest_digest,
                InboxReceipt.verification_config_revision_id,
                InboxReceipt.verification_config_digest,
                InboxReceipt.verification_mode,
            ).where(InboxReceipt.id == receipt_id)
        ).one_or_none()
    if receipt is None:
        raise DeliveryError("original receipt is not retained")
    # The existing owner reads these attributes only; a scalar Row supplies the
    # same shape without consulting or refreshing a caller's dirty ORM entity.
    origin = receipt_verification_origin(receipt)
    if origin is None:
        return None
    return ReceiptOriginEvidence(
        receipt_id=receipt.id,
        payload_digest=receipt.payload_digest,
        verification_origin=origin,
    )


@dataclass(frozen=True, slots=True)
class ReceiptClaim:
    """One worker's exclusive, TIME-BOUNDED right to deliver one receipt.

    `attempt` and `leased_until` together are the claim's identity, and
    settlement presents both. Neither alone is enough: the attempt number says
    WHICH try this is, and the lease says the try is still live.
    """

    receipt_id: UUID
    attempt: int
    leased_until: datetime
    destination: TrustedDestination
    source: ProductObservationSource
    provider_event_id: str
    event_type: str
    observation: Mapping[str, object]
    correlation_id: str
    #: The durable fingerprint. ReceiptClaims returns it even on first dispatch.
    #: Optional for structural claim-store implementations; compared, never overwritten.
    stored_fingerprint: str | None = None
    origin_evidence: ReceiptOriginEvidence | None = None
    delivery_snapshot: DeliverySnapshot | None = None

    def __post_init__(self) -> None:
        if self.attempt < 1:
            raise DeliveryError(
                f"attempt={self.attempt} is not a claim — the conditional UPDATE "
                "increments the counter, so a held claim is always at least 1"
            )
        object.__setattr__(self, "observation", _frozen(self.observation))


def _require_snapshot_consistency(
    claim: ReceiptClaim, *, require_snapshot: bool = False
) -> None:
    """Compare all actual routing/source facts, beyond published wire identity.

    Snapshot-free structural stores retain their existing contract; they do not
    inherit ReceiptClaims' hardened persistence guarantee. The real store always
    requires a snapshot, and every snapshot-bearing custom claim is checked.
    """
    if claim.delivery_snapshot is None and not require_snapshot:
        return
    try:
        if not isinstance(claim.delivery_snapshot, DeliverySnapshot):
            raise ValueError("missing typed dispatch snapshot")
        snapshot = DeliverySnapshot.from_document(claim.delivery_snapshot.document())
        if (
            not isinstance(claim.destination, DestinationBinding)
            or claim.source != snapshot.source
            or claim.destination != snapshot.destination
            or destination_binding_document(claim.destination)
            != destination_binding_document(snapshot.destination)
        ):
            raise ValueError("claim differs from frozen dispatch contract")
    except (DeliveryError, ValueError, TypeError, AttributeError, KeyError) as exc:
        raise DeliveryPreparationRefused("dispatch_identity_conflict") from exc


@dataclass(frozen=True, slots=True)
class ProductRequest:
    """What crosses the boundary — provider-neutral, and fully self-describing.

    ADR-0024 § 3: this is an OBSERVATION. It carries what was seen, addressed to
    a destination that was resolved from trusted state; it assigns no
    authoritative product state and names no product decision. What the product
    does about it is the product's to decide.
    """

    destination: TrustedDestination
    source: ProductObservationSource
    contract_version: int
    idempotency_key: str
    request_fingerprint: str
    correlation_id: str
    receipt_id: UUID
    provider_event_id: str
    event_type: str
    observation: Mapping[str, object]
    origin_evidence: ReceiptOriginEvidence | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "observation", _frozen(self.observation))


class ProductAcceptance(str, Enum):
    """What the product said, in the ENGINE's vocabulary.

    Five answers, because four collapse two genuinely different situations.
    `ALREADY_APPLIED` is not a redundant `ACCEPTED`: it is the evidence that the
    idempotency key did its job, and losing the distinction would hide a
    double-send behind a success. `INDETERMINATE` is not a redundant
    `UNAVAILABLE`: it says retrying is NOT safe, which is the opposite
    instruction.
    """

    #: The product applied the consequence on this call.
    ACCEPTED = "accepted"
    #: The product recognised the idempotency key and did nothing further. The
    #: consequence already exists — a retried timeout lands here.
    ALREADY_APPLIED = "already_applied"
    #: The product refused, and will refuse again. Retrying cannot help.
    REJECTED = "rejected"
    #: Not reached, or answered unusably. Safe to retry: the same idempotency
    #: key is presented again and the product deduplicates.
    UNAVAILABLE = "unavailable"
    #: The connector cannot tell whether the consequence landed AND knows the
    #: key will not protect a retry. A human or a repair command must decide.
    INDETERMINATE = "indeterminate"


#: The one place acceptance becomes a retry decision. A mapping rather than an
#: `if` chain so that adding an acceptance without deciding its retry semantics
#: is a `KeyError` at the boundary instead of a silent fall-through to
#: "retryable" — the default that quietly duplicates consequences.
_ACCEPTANCE_TO_STATUS: Mapping[ProductAcceptance, OutcomeStatus] = MappingProxyType(
    {
        ProductAcceptance.ACCEPTED: OutcomeStatus.SUCCEEDED,
        ProductAcceptance.ALREADY_APPLIED: OutcomeStatus.SUCCEEDED,
        ProductAcceptance.REJECTED: OutcomeStatus.TERMINAL,
        ProductAcceptance.UNAVAILABLE: OutcomeStatus.RETRYABLE,
        ProductAcceptance.INDETERMINATE: OutcomeStatus.RECONCILIATION_REQUIRED,
    }
)


@dataclass(frozen=True, slots=True)
class ProductOutcome:
    """The product's typed answer, plus the evidence a reconciler needs.

    `product_ref` is that evidence: the destination's OWN identifier for what
    the observation became. Without it, reconciling "did this land?" means
    asking the product to search by content, which is exactly the guesswork
    `consequence_json` exists to remove.
    """

    acceptance: ProductAcceptance
    #: The destination's identifier for the resulting record, when it gave one.
    product_ref: str | None = None
    #: The contract version the product ACKNOWLEDGED, which may lag the one
    #: sent. Recorded rather than asserted — a mismatch is an operational fact
    #: for a reconciler, not a reason to discard a consequence that happened.
    acknowledged_contract_version: int | None = None
    error_code: str | None = None
    error_detail: str | None = None
    retry_after_seconds: int | None = None
    evidence: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", _frozen(self.evidence))

    @property
    def consequence_happened(self) -> bool:
        """True when the product holds the consequence, however it got there."""
        return self.acceptance in (
            ProductAcceptance.ACCEPTED,
            ProductAcceptance.ALREADY_APPLIED,
        )

    def as_outcome(self) -> Outcome:
        """Hand the existing retry engine its own vocabulary.

        Reused rather than reimplemented: `retry.next_state` already owns
        attempt exhaustion and backoff, and a second classifier here would be a
        second opinion about when to stop trying.
        """
        return Outcome(
            status=_ACCEPTANCE_TO_STATUS[self.acceptance],
            error_code=self.error_code,
            error_detail=self.error_detail,
            retry_after_seconds=self.retry_after_seconds,
        )


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    """What one pass of :func:`deliver_receipt` did. Never raises to say "busy"."""

    receipt_id: UUID
    claimed: bool
    attempt: int | None = None
    outcome: ProductOutcome | None = None
    #: Why nothing was claimed — another worker holds it, it is not due, or it
    #: is already finished. A caller sweeping a queue needs this to be a value.
    unclaimed_reason: str | None = None
    #: A preparation refusal is distinct from a held/not-due receipt.
    preparation_refused: bool = False


# ── Pure derivations ────────────────────────────────────────────────────────


def idempotency_key_for(*, receipt_id: UUID, destination: TrustedDestination) -> str:
    """The key the PRODUCT deduplicates on. Stable across attempts.

    **The attempt number is deliberately absent**, and that absence is the whole
    at-most-once property of this engine: a timeout on attempt 1 followed by a
    successful attempt 2 must present the product with the same key, or the
    consequence happens twice. `test_the_key_is_stable_across_attempts` pins it.

    The destination IS included, because the same observation legitimately
    delivered to two applications is two consequences, and one shared key would
    make the second look like a duplicate of the first and silently vanish.

    Digested rather than concatenated so the result stays inside the ledger's
    200-character key limit whatever a destination's `ref` looks like, while the
    readable receipt id stays in the clear for an operator reading a log line.
    """
    material = "|".join(
        (
            str(destination.capability_binding_id),
            destination.application,
            destination.scope.kind,
            destination.scope.ref,
            str(destination.contract_version),
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"receipt:{receipt_id}:{digest}"


def request_fingerprint_for(
    *,
    destination: TrustedDestination,
    source: ProductObservationSource,
    provider_event_id: str,
    event_type: str,
    observation: Mapping[str, object],
) -> str:
    """A stable digest of WHAT is being delivered, for replay-vs-conflict.

    Distinct from the idempotency key on purpose: the key says "which delivery
    is this", the fingerprint says "carrying what". Same key + same fingerprint
    is a replay; same key + different fingerprint is a
    :class:`FingerprintConflict`.

    Uses `execution.payload_digest` rather than a second canonical-JSON
    encoder — two digest functions that are supposed to agree eventually do not.
    """
    return payload_digest(
        {
            "application": destination.application,
            "capability_id": destination.capability_id,
            "scope": {
                "kind": destination.scope.kind,
                "ref": destination.scope.ref,
            },
            "contract_version": destination.contract_version,
            "source": {
                "installation_id": str(source.installation_id),
                "connector_key": source.connector_key,
            },
            "provider_event_id": provider_event_id,
            "event_type": event_type,
            "observation": observation,
        }
    )


def require_stable_fingerprint(stored: str | None, computed: str) -> None:
    """Refuse a replay that changed its content.

    `None` is a first attempt and always passes. Anything else must match
    exactly — there is no "close enough", because the alternative to raising is
    overwriting a recorded consequence with content it does not describe.
    """
    if stored is not None and stored != computed:
        raise FingerprintConflict(
            f"this delivery was previously attempted with fingerprint "
            f"{stored[:16]}… and now carries {computed[:16]}…. Same identity, "
            "different request: refusing to overwrite the recorded consequence. "
            "A genuinely different observation is a different receipt"
        )


def build_product_request(claim: ReceiptClaim) -> ProductRequest:
    """Assemble the outbound request from the CLAIM, and only from the claim.

    **This signature is the security property**, in the same sense Team 3's
    `resolve_destination` is: there is no payload parameter, no header map and
    no application name among its inputs, so no provider-influenced value can
    select or redirect a destination. Everything addressing the product comes
    from `claim.destination`, which the claim transaction resolved from an
    immutable config revision.

    Guarded by `test_delivery_is_addressed_only_from_trusted_state`.
    """
    fingerprint = request_fingerprint_for(
        destination=claim.destination,
        source=claim.source,
        provider_event_id=claim.provider_event_id,
        event_type=claim.event_type,
        observation=claim.observation,
    )
    request = ProductRequest(
        destination=claim.destination,
        source=claim.source,
        contract_version=claim.destination.contract_version,
        idempotency_key=idempotency_key_for(
            receipt_id=claim.receipt_id, destination=claim.destination
        ),
        request_fingerprint=fingerprint,
        correlation_id=claim.correlation_id,
        receipt_id=claim.receipt_id,
        provider_event_id=claim.provider_event_id,
        event_type=claim.event_type,
        observation=claim.observation,
        origin_evidence=claim.origin_evidence,
    )
    if (
        _wire_schema_version(claim.destination)
        == PRODUCT_OBSERVATION_ORIGIN_SCHEMA_VERSION
    ):
        fingerprint = payload_digest(product_observation_document(request))
        request = dataclass_replace(request, request_fingerprint=fingerprint)
    require_stable_fingerprint(claim.stored_fingerprint, fingerprint)
    return request


PRODUCT_OBSERVATION_SCHEMA_VERSION = "dotmac.io/product-observation/v1"
PRODUCT_OBSERVATION_ORIGIN_SCHEMA_VERSION = "dotmac.io/product-observation/v2"


def _wire_schema_version(destination: TrustedDestination) -> str:
    # Legacy structural destinations and pre-v3 descriptors retain v1 exactly.
    descriptor = getattr(destination, "product_port", None)
    version = getattr(descriptor, "wire_schema_version", None)
    if version is None:
        return PRODUCT_OBSERVATION_SCHEMA_VERSION
    if not isinstance(version, str) or version not in (
        PRODUCT_OBSERVATION_SCHEMA_VERSION,
        PRODUCT_OBSERVATION_ORIGIN_SCHEMA_VERSION,
        "dotmac.io/integrator-observation-envelope/v1",
    ):
        raise DeliveryError("unsupported product observation wire schema")
    return version


def _origin_document(request: ProductRequest) -> dict[str, object]:
    evidence = request.origin_evidence
    if evidence is None:
        raise DeliveryError(
            "v2 requires retained original receipt context; history is unknown"
        )
    if not isinstance(evidence, ReceiptOriginEvidence):
        raise DeliveryError("v2 requires typed original receipt context")
    try:
        ReceiptOriginEvidence.__post_init__(evidence)
    except (AttributeError, TypeError, ValueError) as exc:
        raise DeliveryError("original receipt context is invalid") from exc
    origin = evidence.verification_origin
    if (
        evidence.receipt_id != request.receipt_id
        or origin.installation_id != request.source.installation_id
        or origin.connector_key != request.source.connector_key
        or origin.capability_binding_id != request.destination.capability_binding_id
        or evidence.payload_digest != payload_digest(_json_value(request.observation))
    ):
        raise DeliveryError("original receipt context disagrees with the delivery")
    return {
        "receipt_id": str(evidence.receipt_id),
        "payload_digest": evidence.payload_digest,
        "installation_id": str(origin.installation_id),
        "capability_binding_id": str(origin.capability_binding_id),
        "connector_key": origin.connector_key,
        "manifest_digest": origin.manifest_digest,
        "original_config_digest": origin.config_digest,
        "config_revision_id": (
            None
            if origin.config_revision_id is None
            else str(origin.config_revision_id)
        ),
        "verification_mode": origin.verification_mode,
    }


def product_observation_document(request: ProductRequest) -> dict[str, object]:
    """Project the explicitly negotiated ProductObservation wire document.

    Addressing comes only from the immutable product descriptor resolved during
    the claim. Source provenance comes only from the module's installation and
    binding rows for v1, and only retained original receipt context for v2.
    Unknown v2 history refuses. The connector-normalized observation is product
    input, never interpreted here.
    """

    version = _wire_schema_version(request.destination)
    if version == "dotmac.io/integrator-observation-envelope/v1":
        # This builder historically emitted v1; the assembly owns the distinct
        # messaging envelope adapter and still selects it explicitly.
        version = PRODUCT_OBSERVATION_SCHEMA_VERSION
    document: dict[str, object] = {
        "schema_version": version,
        "capability_id": request.destination.capability_id,
        "contract_version": request.contract_version,
        "source": {
            "installation_id": str(request.source.installation_id),
            "connector_key": request.source.connector_key,
        },
        "provider_event_id": request.provider_event_id,
        "event_type": request.event_type,
        "scope": {
            "kind": request.destination.scope.kind,
            "ref": request.destination.scope.ref,
        },
        "observation": _json_value(request.observation),
    }
    if version == PRODUCT_OBSERVATION_ORIGIN_SCHEMA_VERSION:
        document["receipt_origin"] = _origin_document(request)
    return document


# ── The two seams ───────────────────────────────────────────────────────────


class ProductPortClient(Protocol):
    """The product's PORT, spoken to over the network. Never implemented here.

    Named for what it is a client OF. "Gateway" invited the reading that this
    seam owns the delivery — including the claim and the settle — and an
    earlier draft did exactly that, leaving the module unable to deliver
    anything on its own. Claiming and settling are this module's, against its
    own table; the only thing it cannot do itself is speak the product's
    protocol, and that is all this is.

    Takes a :class:`ProductRequest` and returns a :class:`ProductOutcome`, or
    raises :class:`TransportFailure`. It receives NO session — that is not an
    oversight to be fixed by a convenience parameter, it is phase 2's contract.
    """

    def deliver(self, request: ProductRequest) -> ProductOutcome: ...


class ReceiptClaimStore(Protocol):
    """The two transactions. One implementation, backed by `inbox_receipts`.

    A PORT, not a second ledger: it owns no state of its own and its
    implementation is a conditional UPDATE against the receipt row. It exists so
    phase ordering is expressible and testable independently of the migration
    that adds the columns — see this module's `# BLOCKED` note in the package
    changelog.

    Both methods own their own transaction and hold it for no longer than the
    statement takes. Neither may be called while the product is being contacted.
    """

    def claim(
        self, *, receipt_id: UUID, now: datetime | None = None
    ) -> ReceiptClaim | None:
        """Phase 1. `None` when another worker holds it, it is not due, or it
        is already finished — a value, not an exception, because a sweeper
        finding nothing to do is the normal case."""
        ...

    def settle(self, claim: ReceiptClaim, outcome: ProductOutcome) -> bool:
        """Phase 3. False when the claim is no longer held.

        The implementation's UPDATE must carry the claim's identity in its
        WHERE clause — receipt, attempt, and a lease that has not expired — so
        that `rowcount == 0` is the database saying this worker was superseded.
        """
        ...


#: Seconds a claim is held for. Long enough that an ordinary product call
#: finishes inside it, short enough that a worker killed mid-call frees the
#: receipt in a time an operator will wait. A knob with a documented default,
#: not a constant buried in a WHERE clause.
DEFAULT_LEASE_SECONDS = 300

#: States a delivery can no longer be attempted from — finished, or escalated
#: out of the automatic path. `retryable` is deliberately absent: a receipt that
#: backed off is claimable again once `next_attempt_at` passes.
_TERMINAL_STATES = ("processed", "dead_letter", "reconciliation_required")

#: `retry.next_state` speaks the OUTBOX's vocabulary, where success is
#: "delivered". The inbox calls the same state `processed`. Mapped rather than
#: renamed on either side, because the two lifecycles are genuinely different
#: and collapsing them would make one table's states depend on the other's.
_DELIVERY_TO_RECEIPT_STATE = {"delivered": "processed"}


def _receipt_state_for(outcome: ProductOutcome, *, attempt: int) -> str:
    """The receipt state this outcome produces.

    Delegates the decision to `retry.next_state`, which already owns attempt
    exhaustion and backoff. A second classifier here would be a second opinion
    about when to stop trying — and the one that disagreed would win by being
    called last.
    """
    state = next_state(outcome.as_outcome(), attempt_count=attempt)
    return _DELIVERY_TO_RECEIPT_STATE.get(state, state)


#: The terminal states, ready to be inlined into a predicate. Built from
#: `_TERMINAL_STATES` so the statement and the tuple cannot disagree; every
#: element is a literal this module owns, never caller input.
_TERMINAL_SQL = ", ".join(f"'{state}'" for state in _TERMINAL_STATES)


def claim_statement(
    *, lease_seconds: int = DEFAULT_LEASE_SECONDS, clock: str = "now()"
) -> Any:
    """Phase 1's conditional UPDATE, as a statement a test can execute.

    Exposed rather than built inline so the Postgres canaries execute the
    statement that SHIPS. A test that retypes the SQL proves its own copy
    races correctly and says nothing about the module — the class of mistake
    this file's own docstring warns about for fakes.

    `clock` is a SQL expression, not a value: `now()` in production, a bound
    parameter when a test needs to drive time without waiting for it.
    """
    from sqlalchemy import text

    return text(
        f"UPDATE {SCHEMA}.inbox_receipts SET state = 'processing', "  # noqa: S608 # nosec B608 -- every interpolated fragment is a module-owned literal (schema name, state names, an int lease); all VALUES are bound
        "attempt_count = attempt_count + 1, "
        f"leased_until = {clock} + make_interval(secs => {lease_seconds:d}) "
        "WHERE id = :id "
        f"AND state NOT IN ({_TERMINAL_SQL}) "
        f"AND (leased_until IS NULL OR leased_until < {clock}) "
        f"AND (next_attempt_at IS NULL OR next_attempt_at <= {clock}) "
        "RETURNING attempt_count, leased_until, provider_event_id, event_type, "
        "payload_json, correlation_id, delivery_fingerprint, capability_binding_id, "
        "installation_id, delivery_snapshot_json, delivery_idempotency_key, "
        "destination_application, destination_contract_version, destination_revision_id"
    )


def settle_statement() -> Any:
    """Phase 3's conditional UPDATE, guarded by the claim's own identity.

    The WHERE clause is the whole point: this receipt, this attempt, and a
    lease that has not expired. Exposed for the same reason as
    :func:`claim_statement`.
    """
    from sqlalchemy import text

    return text(
        f"UPDATE {SCHEMA}.inbox_receipts SET state = :state, "  # noqa: S608 # nosec B608 -- every interpolated fragment is a module-owned literal (schema name, state names, an int lease); all VALUES are bound
        "leased_until = NULL, processed_at = now(), "
        "product_acceptance = :acceptance, product_ref = :product_ref, "
        "error_code = :error_code, error_detail = :error_detail, "
        "next_attempt_at = CASE WHEN CAST(:backoff AS integer) IS NULL "
        "  THEN NULL ELSE now() + make_interval(secs => :backoff) END "
        "WHERE id = :id AND state = 'processing' AND attempt_count = :attempt "
        "AND leased_until IS NOT NULL AND leased_until = :leased_until "
        "AND leased_until >= now() "
        "AND delivery_snapshot_json->>'state' = 'frozen' "
        "AND delivery_fingerprint = :fingerprint "
        "AND delivery_idempotency_key = :idempotency_key "
        "AND correlation_id = :correlation_id "
        "AND destination_application = :application "
        "AND destination_contract_version = :contract_version "
        "AND destination_revision_id = :revision_id "
        "AND delivery_snapshot_json = CAST(:snapshot AS jsonb)"
    )


class ReceiptClaims:
    """The real store: conditional UPDATEs against `inbox_receipts`.

    This is the module's OWN persistence, not an injected collaborator. The
    engine previously had only the :class:`ReceiptClaimStore` protocol, so the
    shipped package could not claim or settle anything without a caller
    supplying the very mechanism the design is about — and the SQL defining
    at-most-once lived outside the package that owns at-most-once.

    Both methods own their transaction and hold it only as long as the
    statement takes. Neither may be called while the product is being
    contacted; that is phase 2's contract, and it is why these are two methods
    rather than one `with` block.
    """

    def __init__(
        self,
        session_factory: Any,
        *,
        registry: CapabilityRegistry,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        #: A CALLABLE returning a session, not a session. The two phases are
        #: separate transactions by design; sharing one session would reduce
        #: that to the caller remembering to commit in the right places.
        self._session_factory = session_factory
        self._registry = registry
        self._lease_seconds = lease_seconds

    def claim(
        self, *, receipt_id: UUID, now: datetime | None = None
    ) -> ReceiptClaim | None:
        """Phase 1. `rowcount == 1` IS the claim.

        The predicate — not terminal, not held by a live lease, and due — is
        evaluated BY THE DATABASE inside the UPDATE. The loser of a race sees
        zero rows and gets `None`. There is no window between the check and the
        write because there is no separate check.

        The first dispatch freezes the selected contract and request identity
        in this transaction. A retry reconstructs that contract from the receipt
        and corroborates live authority without choosing a replacement route.
        No dispatchable result leaves before that transaction commits.
        """
        clock = "now()" if now is None else "CAST(:now AS timestamptz)"
        claim_sql = claim_statement(lease_seconds=self._lease_seconds, clock=clock)

        parameters: dict[str, object] = {"id": receipt_id}
        if now is not None:
            parameters["now"] = now

        with self._session_factory() as session:
            row = session.execute(claim_sql, parameters).first()
            if row is None:
                # Nothing was claimed, so there is nothing to roll back — but
                # the transaction is closed explicitly rather than left to the
                # context manager's default, which differs between session
                # configurations.
                session.rollback()
                return None

            from sqlalchemy import text

            try:
                claim = self._prepare(session, receipt_id=receipt_id, row=row)
                _require_snapshot_consistency(claim, require_snapshot=True)
                request = build_product_request(claim)
                if claim.delivery_snapshot is None:
                    raise DeliveryPreparationRefused("dispatch_snapshot_unavailable")
                snapshot = claim.delivery_snapshot.document()
                session.execute(
                    text(
                        f"UPDATE {SCHEMA}.inbox_receipts SET "  # noqa: S608 # nosec B608 -- module-owned schema; values bound
                        "delivery_snapshot_json = CAST(:snapshot AS jsonb), "
                        "delivery_fingerprint = :fingerprint, "
                        "delivery_idempotency_key = :idempotency_key, "
                        "correlation_id = :correlation_id, "
                        "destination_application = :application, "
                        "destination_contract_version = :contract_version, "
                        "destination_revision_id = :revision_id WHERE id = :id "
                        "AND delivery_snapshot_json->>'state' = 'unstarted'"
                    ),
                    {
                        "id": receipt_id,
                        "snapshot": json.dumps(snapshot),
                        "fingerprint": request.request_fingerprint,
                        "idempotency_key": request.idempotency_key,
                        "correlation_id": request.correlation_id,
                        "application": claim.destination.application,
                        "contract_version": claim.destination.contract_version,
                        "revision_id": claim.destination.destination_revision_id,
                    },
                )
            except DeliveryPreparationRefused as refusal:
                session.execute(
                    text(
                        f"UPDATE {SCHEMA}.inbox_receipts SET "  # noqa: S608 # nosec B608 -- module-owned schema; values bound
                        "state = 'reconciliation_required', leased_until = NULL, "
                        "next_attempt_at = NULL, product_acceptance = 'indeterminate', "
                        "error_code = :code, error_detail = :detail WHERE id = :id"
                    ),
                    {
                        "id": receipt_id,
                        "code": refusal.error_code,
                        "detail": refusal.error_code,
                    },
                )
                session.commit()
                raise
            session.commit()
        return dataclass_replace(claim, stored_fingerprint=request.request_fingerprint)

    def _prepare(self, session: Any, *, receipt_id: UUID, row: Any) -> ReceiptClaim:
        """Freeze once; corroborate live authority without selecting a retry route."""
        from sqlalchemy import select

        value = row.delivery_snapshot_json
        unstarted = (
            isinstance(value, dict)
            and value == _UNSTARTED_SNAPSHOT
            and type(value.get("schema_version")) is int
        )
        snapshot = None if unstarted else DeliverySnapshot.from_document(value)
        if unstarted and any(
            item is not None
            for item in (
                row.delivery_fingerprint,
                row.delivery_idempotency_key,
                row.destination_application,
                row.destination_contract_version,
                row.destination_revision_id,
            )
        ):
            raise DeliveryPreparationRefused("dispatch_snapshot_unavailable")
        try:
            live_destination = resolve_destination(
                session,
                capability_binding_id=row.capability_binding_id,
                registry=self._registry,
            )
            live_capability = self._registry.get(live_destination.capability_id)
            live_source = resolve_product_observation_source(
                session,
                capability_binding_id=row.capability_binding_id,
            )
            authority = session.execute(
                select(CapabilityBinding.state, ConnectorInstallation.state)
                .join(
                    ConnectorInstallation,
                    ConnectorInstallation.id == CapabilityBinding.installation_id,
                )
                .where(CapabilityBinding.id == row.capability_binding_id)
            ).one_or_none()
            if (
                authority is None
                or tuple(authority) != ("enabled", "enabled")
                or live_source.installation_id != row.installation_id
                or (
                    live_destination.product_port is not None
                    and live_destination.product_port.activation_state != "enabled"
                )
            ):
                raise DeliveryPreparationRefused("dispatch_authority_changed")
        except (DestinationBindingError, DeliveryError, CapabilityRegistryError) as exc:
            raise DeliveryPreparationRefused("dispatch_authority_changed") from exc
        if snapshot is None:
            snapshot = DeliverySnapshot.from_document(
                DeliverySnapshot(
                    source=live_source,
                    destination=live_destination,
                    capability=live_capability,
                ).document()
            )
        elif (
            snapshot.source != live_source
            or snapshot.capability != live_capability
            or snapshot.document()["destination"]
            != destination_binding_document(live_destination)
        ):
            raise DeliveryPreparationRefused("dispatch_authority_changed")
        from dotmac_integration.retention import is_redacted

        if not isinstance(row.payload_json, dict) or is_redacted(row):
            # Retention clears the sole payload; never reconstruct it from a snapshot.
            raise DeliveryPreparationRefused("dispatch_payload_unavailable")
        origin = resolve_receipt_origin_evidence(session, receipt_id=receipt_id)
        claim = ReceiptClaim(
            receipt_id=receipt_id,
            attempt=row.attempt_count,
            leased_until=row.leased_until,
            destination=snapshot.destination,
            source=snapshot.source,
            provider_event_id=row.provider_event_id,
            event_type=row.event_type,
            observation=row.payload_json,
            correlation_id=row.correlation_id or str(receipt_id),
            stored_fingerprint=row.delivery_fingerprint,
            origin_evidence=origin,
            delivery_snapshot=snapshot,
        )
        try:
            _require_snapshot_consistency(claim, require_snapshot=True)
            request = build_product_request(claim)
        except DeliveryError as exc:
            raise DeliveryPreparationRefused("dispatch_identity_conflict") from exc
        if not unstarted and (
            row.delivery_fingerprint != request.request_fingerprint
            or row.delivery_idempotency_key != request.idempotency_key
            or row.correlation_id != request.correlation_id
            or row.destination_application != snapshot.destination.application
            or row.destination_contract_version != snapshot.destination.contract_version
            or row.destination_revision_id
            != snapshot.destination.destination_revision_id
        ):
            raise DeliveryPreparationRefused("dispatch_identity_conflict")
        return claim

    def settle(self, claim: ReceiptClaim, outcome: ProductOutcome) -> bool:
        """Phase 3. `False` when this worker was superseded.

        The WHERE clause carries the claim's whole identity — this receipt,
        this attempt, and a lease that has not expired. A worker whose lease ran
        out mid-call matches nothing, and `rowcount == 0` is the database saying
        so. Recording its outcome anyway would overwrite the result of whichever
        worker legitimately took over.

        Frozen request identity is only compared here. Settlement cannot
        establish or alter routing, fingerprint, key, or correlation.
        """
        if claim.delivery_snapshot is None:
            return False
        try:
            _require_snapshot_consistency(claim, require_snapshot=True)
        except DeliveryPreparationRefused:
            return False
        request = build_product_request(claim)
        state = _receipt_state_for(outcome, attempt=claim.attempt)
        backoff = outcome.retry_after_seconds if state == "retryable" else None

        statement = settle_statement()
        with self._session_factory() as session:
            result = session.execute(
                statement,
                {
                    "id": claim.receipt_id,
                    "attempt": claim.attempt,
                    "leased_until": claim.leased_until,
                    "application": claim.destination.application,
                    "contract_version": claim.destination.contract_version,
                    "revision_id": claim.destination.destination_revision_id,
                    "snapshot": json.dumps(claim.delivery_snapshot.document()),
                    "state": state,
                    "acceptance": outcome.acceptance.value,
                    "product_ref": outcome.product_ref,
                    "error_code": outcome.error_code,
                    "error_detail": outcome.error_detail,
                    "fingerprint": request.request_fingerprint,
                    "idempotency_key": request.idempotency_key,
                    "correlation_id": claim.correlation_id,
                    "backoff": backoff,
                },
            )
            claimed = result.rowcount == 1
            session.commit()
        return bool(claimed)


# ── The orchestrator ────────────────────────────────────────────────────────


def deliver_receipt(
    *,
    receipt_id: UUID,
    store: ReceiptClaimStore,
    gateway: ProductPortClient,
    now: datetime | None = None,
) -> DeliveryReport:
    """Claim, call, settle — with no session held across the call.

    The phase boundaries are visible in this function's shape, which is the
    point: `store.claim` returns before `gateway.deliver` is entered, and
    `store.settle` is not reached until it returns. Nothing here holds a
    transaction open around the network.

    :raises LostClaim: settlement was refused because the lease expired and
        another worker took over. Raised, never swallowed — see
        :class:`LostClaim`.
    :raises FingerprintConflict: the request changed under an unchanged
        identity. Raised BEFORE the product is contacted, so a conflicting
        replay never reaches the network.
    """
    try:
        claim = store.claim(receipt_id=receipt_id, now=now)
        if claim is not None:
            _require_snapshot_consistency(claim)
    except DeliveryPreparationRefused as refusal:
        return DeliveryReport(
            receipt_id=receipt_id,
            claimed=False,
            preparation_refused=True,
            outcome=ProductOutcome(
                acceptance=ProductAcceptance.INDETERMINATE,
                error_code=refusal.error_code,
                error_detail=refusal.error_code,
            ),
        )
    if claim is None:
        return DeliveryReport(
            receipt_id=receipt_id,
            claimed=False,
            unclaimed_reason="not claimable: held, not due, or already finished",
        )

    # Built BEFORE the call and outside any transaction. A fingerprint conflict
    # must fail here rather than after the product has been told something.
    request = build_product_request(claim)

    # ── Phase 2. No session. No transaction. Only the network. ──────────────
    try:
        outcome = gateway.deliver(request)
    except TransportFailure as failure:
        # Retryable, and safe to retry, because `request.idempotency_key` is the
        # same one the next attempt will present. See the module docstring.
        outcome = ProductOutcome(
            acceptance=ProductAcceptance.UNAVAILABLE,
            error_code=failure.error_code,
            error_detail=str(failure),
        )

    if not store.settle(claim, outcome):
        raise LostClaim(
            f"receipt {receipt_id} attempt {claim.attempt} could not be settled: "
            "the lease expired and another worker took it over. This worker's "
            "outcome is stale and was NOT recorded"
        )

    return DeliveryReport(
        receipt_id=receipt_id,
        claimed=True,
        attempt=claim.attempt,
        outcome=outcome,
    )
