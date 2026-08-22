"""The three-phase POLL seam — prepare, invoke, record-and-advance.

POLL was executable at the plugin boundary before it was executable by the
engine. This module closes that gap without creating a second inbox or
checkpoint mechanism:

=========================  ================================================
``prepare_poll``           resolve binding, installation, manifest pin,
                           immutable config and checkpoint version. Short DB.
``invoke_poll``            materialize held secrets and call the plugin.
                           No session or transaction by signature.
``record_poll_batch``      atomically record the whole event tuple and advance
                           the optimistic checkpoint. Short DB.
=========================  ================================================

The last phase is one transaction because the cursor is a claim about durable
receipts. Advancing it after a partial or rolled-back batch permanently skips
provider facts; committing receipts and losing the cursor makes them safe to
redrive because inbox deduplication is binding-scoped.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.execution import advance_checkpoint
from dotmac_integration.ingress import record_batch
from dotmac_integration.models import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    PollingCheckpoint,
)
from dotmac_integration.selection import _usable
from dotmac_integration.spi import (
    ConnectorMode,
    InboundEvent,
    PollHandler,
    PollPlugin,
    accepts_manifest_digest,
)

__all__ = [
    "CursorInvalid",
    "PollBatch",
    "PollConnectorRaised",
    "PollContractError",
    "PollError",
    "PollHandlerUnavailable",
    "PollResult",
    "PollSecretsUnavailable",
    "PollUnavailable",
    "PreparedPoll",
    "invoke_poll",
    "poll_once",
    "prepare_poll",
    "record_poll_batch",
]

SecretResolver = Callable[[Mapping[str, str]], Mapping[str, str]]
UnitOfWork = Callable[[], AbstractContextManager[Any]]


class PollError(RuntimeError):
    """A polling cycle could not complete safely."""


class PollUnavailable(PollError):
    """Durable configuration cannot currently serve this polling job."""


class CursorInvalid(PollUnavailable):
    """A checkpoint is not in the opaque cursor envelope this engine owns."""


class PollHandlerUnavailable(PollUnavailable):
    """The connector declares POLL but cannot serve this capability."""


class PollSecretsUnavailable(PollError):
    """Held material could not be resolved; resolver details stay private."""


class PollContractError(PollError):
    """A connector returned something outside the POLL protocol."""


class PollConnectorRaised(PollError):
    """A connector raised; only the exception type is safe to expose."""

    def __init__(self, exception_type: str) -> None:
        safe = exception_type if exception_type.isidentifier() else "Exception"
        super().__init__(f"poll connector raised {safe}")


@dataclass(frozen=True, slots=True)
class PreparedPoll:
    """Everything phase 2 needs, detached from the phase-1 session."""

    checkpoint_id: UUID
    expected_version: int
    installation_id: UUID
    binding_id: UUID
    connector_key: str
    capability_id: str
    job_key: str
    cursor: str | None
    config: dict[str, object]
    secret_refs: dict[str, str]
    config_revision_id: UUID | None


@dataclass(frozen=True, slots=True, repr=False)
class PollBatch:
    """Normalized provider facts plus the cursor that follows all of them."""

    events: tuple[InboundEvent, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class PollResult:
    """Detached values safe to read after the unit of work closes."""

    checkpoint_id: UUID
    checkpoint_version: int
    receipt_ids: tuple[UUID, ...]
    recorded: int
    duplicates: int


def _cursor(value: dict[str, object] | None) -> str | None:
    if value is None:
        return None
    if set(value) != {"cursor"}:
        raise CursorInvalid(
            "polling checkpoint is not the engine-owned opaque cursor envelope"
        )
    cursor = value["cursor"]
    if cursor is not None and not isinstance(cursor, str):
        raise CursorInvalid("polling checkpoint cursor must be a string or null")
    return cursor


def prepare_poll(
    db: Any,
    *,
    checkpoint_id: UUID,
    registry: ConnectorRegistry,
) -> PreparedPoll:
    """Resolve and pin a polling job. Database, short, writes nothing."""
    checkpoint = db.get(PollingCheckpoint, checkpoint_id)
    if checkpoint is None:
        raise PollUnavailable("polling checkpoint does not exist")
    binding = db.get(CapabilityBinding, checkpoint.capability_binding_id)
    if binding is None:
        raise PollUnavailable("polling checkpoint has no capability binding")
    installation = db.get(ConnectorInstallation, binding.installation_id)
    if installation is None or not _usable(binding, installation):
        raise PollUnavailable("polling binding or installation is not enabled")

    try:
        registry.require_compatible(installation.connector_key)
        plugin = registry.plugin(installation.connector_key)
    except Exception:
        raise PollUnavailable("poll connector is not installed or compatible") from None
    if not accepts_manifest_digest(plugin, installation.manifest_digest):
        raise PollUnavailable("poll connector no longer honours the manifest pin")
    if ConnectorMode.POLL not in plugin.modes or not isinstance(plugin, PollPlugin):
        raise PollUnavailable("binding does not resolve to an executable poll mode")

    revision = (
        db.get(ConnectorConfigRevision, installation.current_config_revision_id)
        if installation.current_config_revision_id
        else None
    )
    return PreparedPoll(
        checkpoint_id=checkpoint.id,
        expected_version=checkpoint.version,
        installation_id=installation.id,
        binding_id=binding.id,
        connector_key=installation.connector_key,
        capability_id=binding.capability_id,
        job_key=checkpoint.job_key,
        cursor=_cursor(checkpoint.cursor_json),
        config=dict((revision.config_json if revision else {}) or {}),
        secret_refs=dict((revision.secret_refs if revision else {}) or {}),
        config_revision_id=revision.id if revision else None,
    )


def _handler(prepared: PreparedPoll, registry: ConnectorRegistry) -> PollHandler:
    plugin = registry.plugin(prepared.connector_key)
    if not isinstance(plugin, PollPlugin):  # pragma: no cover - phase 1 refuses
        raise PollHandlerUnavailable("connector does not implement poll mode")
    try:
        handler = plugin.poll_handler_for(prepared.capability_id)
    except Exception:
        raise PollHandlerUnavailable(
            "connector has no handler for capability"
        ) from None
    if not isinstance(handler, PollHandler):
        raise PollHandlerUnavailable("connector returned the wrong poll handler shape")
    return handler


def invoke_poll(
    prepared: PreparedPoll,
    *,
    registry: ConnectorRegistry,
    resolve_secrets: SecretResolver,
) -> PollBatch:
    """Call the connector. No session or transaction exists by signature."""
    handler = _handler(prepared, registry)
    try:
        secrets = dict(resolve_secrets(prepared.secret_refs))
    except Exception:
        raise PollSecretsUnavailable("poll secret material is unavailable") from None
    try:
        try:
            returned = handler.poll(
                prepared.cursor,
                config=prepared.config,
                secrets=secrets,
            )
        except Exception as exc:
            raise PollConnectorRaised(type(exc).__name__) from None
    finally:
        secrets = {}

    if not isinstance(returned, tuple) or len(returned) != 2:
        raise PollContractError("poll handler must return events and a cursor")
    events, next_cursor = returned
    if not isinstance(events, tuple) or not all(
        isinstance(event, InboundEvent) for event in events
    ):
        raise PollContractError("poll handler events must be a tuple of InboundEvent")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise PollContractError("poll handler cursor must be a string or null")
    return PollBatch(events=events, next_cursor=next_cursor)


def record_poll_batch(
    db: Any,
    prepared: PreparedPoll,
    batch: PollBatch,
) -> PollResult:
    """Record every event and advance the cursor in the same transaction."""
    checkpoint = db.get(PollingCheckpoint, prepared.checkpoint_id)
    if checkpoint is None:
        raise PollUnavailable("polling checkpoint disappeared before settlement")

    recorded = record_batch(db, prepared, batch.events)
    advance_checkpoint(
        db,
        checkpoint=checkpoint,
        cursor={"cursor": batch.next_cursor},
        expected_version=prepared.expected_version,
    )
    return PollResult(
        checkpoint_id=checkpoint.id,
        checkpoint_version=checkpoint.version,
        receipt_ids=tuple(receipt_id for receipt_id, _ in recorded),
        recorded=sum(1 for _, is_new in recorded if is_new),
        duplicates=sum(1 for _, is_new in recorded if not is_new),
    )


def poll_once(
    *,
    checkpoint_id: UUID,
    registry: ConnectorRegistry,
    resolve_secrets: SecretResolver,
    unit_of_work: UnitOfWork,
) -> PollResult:
    """Run the three phases with no transaction spanning provider I/O."""
    with unit_of_work() as db:
        prepared = prepare_poll(db, checkpoint_id=checkpoint_id, registry=registry)
    batch = invoke_poll(
        prepared,
        registry=registry,
        resolve_secrets=resolve_secrets,
    )
    with unit_of_work() as db:
        return record_poll_batch(db, prepared, batch)
