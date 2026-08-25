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
from typing import TYPE_CHECKING, Any, Final, cast
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

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dotmac_integration.capability_registry import CapabilityRegistry

__all__ = [
    "CheckpointCreationRaced",
    "CheckpointDefinitionConflict",
    "CheckpointLifecycleError",
    "CursorInvalid",
    "PollBatch",
    "PollConnectorRaised",
    "PollContractError",
    "PollError",
    "PollHandlerUnavailable",
    "PollResult",
    "PollSecretsUnavailable",
    "PollUnavailable",
    "PollingCheckpointPage",
    "PollingCheckpointRef",
    "PollingCheckpointRegistration",
    "PreparedPoll",
    "enabled_polling_checkpoint_page",
    "ensure_polling_checkpoint",
    "invoke_poll",
    "poll_once",
    "prepare_poll",
    "record_poll_batch",
]

SecretResolver = Callable[[Mapping[str, str]], Mapping[str, str]]
UnitOfWork = Callable[[], AbstractContextManager[Any]]


class _InitialCursorUnspecified:
    __slots__ = ()


_INITIAL_CURSOR_UNSPECIFIED: Final[_InitialCursorUnspecified] = (
    _InitialCursorUnspecified()
)


class PollError(RuntimeError):
    """A polling cycle could not complete safely."""


class CheckpointLifecycleError(PollError):
    """A polling checkpoint could not be declared safely."""


class CheckpointDefinitionConflict(CheckpointLifecycleError):
    """The checkpoint key already names a different initial position."""


class CheckpointCreationRaced(CheckpointLifecycleError):
    """Another transaction created the checkpoint outside this snapshot."""


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


@dataclass(frozen=True, slots=True)
class PollingCheckpointRef:
    """A detached checkpoint value safe to return beyond the transaction."""

    id: UUID
    capability_binding_id: UUID
    job_key: str
    version: int
    cursor: str | None


@dataclass(frozen=True, slots=True)
class PollingCheckpointRegistration:
    """The idempotent result of declaring one polling job."""

    checkpoint: PollingCheckpointRef
    created: bool


@dataclass(frozen=True, slots=True)
class PollingCheckpointPage:
    """A stable, bounded page for an assembly scheduler to delegate."""

    checkpoint_ids: tuple[UUID, ...]
    next_after: UUID | None


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


def _checkpoint_ref(checkpoint: PollingCheckpoint) -> PollingCheckpointRef:
    return PollingCheckpointRef(
        id=checkpoint.id,
        capability_binding_id=checkpoint.capability_binding_id,
        job_key=checkpoint.job_key,
        version=checkpoint.version,
        cursor=_cursor(checkpoint.cursor_json),
    )


def _poll_binding(
    db: Any,
    *,
    binding_id: UUID,
    registry: ConnectorRegistry,
    require_enabled: bool,
) -> tuple[CapabilityBinding, ConnectorInstallation]:
    """Resolve one POLL-capable binding without duplicating mode rules."""

    binding = db.get(CapabilityBinding, binding_id)
    if binding is None:
        raise CheckpointLifecycleError("polling capability binding does not exist")
    installation = db.get(ConnectorInstallation, binding.installation_id)
    if installation is None:
        raise CheckpointLifecycleError("polling binding has no connector installation")
    if require_enabled and not _usable(binding, installation):
        raise PollUnavailable("polling binding or installation is not enabled")

    try:
        registry.require_compatible(installation.connector_key)
        plugin = registry.plugin(installation.connector_key)
    except Exception:
        raise CheckpointLifecycleError(
            "poll connector is not installed or compatible"
        ) from None
    if not accepts_manifest_digest(plugin, installation.manifest_digest):
        raise CheckpointLifecycleError(
            "poll connector no longer honours the manifest pin"
        )
    if ConnectorMode.POLL not in plugin.modes or not isinstance(plugin, PollPlugin):
        raise CheckpointLifecycleError(
            "polling binding does not resolve to an executable poll mode"
        )
    try:
        capability = plugin.manifest.require_declares(binding.capability_id)
    except Exception:
        raise CheckpointLifecycleError(
            "polling binding capability is absent from the pinned manifest"
        ) from None
    if capability.modes is not None and ConnectorMode.POLL not in capability.modes:
        raise CheckpointLifecycleError(
            "polling binding capability is not mapped to poll mode"
        )
    return binding, installation


def ensure_polling_checkpoint(
    db: Any,
    *,
    capability_binding_id: UUID,
    job_key: str,
    registry: ConnectorRegistry,
    initial_cursor: str | None | _InitialCursorUnspecified = (
        _INITIAL_CURSOR_UNSPECIFIED
    ),
) -> PollingCheckpointRegistration:
    """Idempotently declare one polling job, without offering rewind/delete.

    The unique ``(binding, job_key)`` pair is the identity. Replaying the same
    declaration returns its detached snapshot; asking that identity to start
    at another cursor is refused because accepting it would be an implicit
    rewind (or skip). The caller owns the transaction: this function mutates
    and flushes, but never commits, rolls back, or opens a session.
    """

    from dotmac_kernel.db import conflict_savepoint
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    normalized_job_key = job_key.strip()
    if not normalized_job_key:
        raise CheckpointLifecycleError("polling checkpoint job key is required")
    if len(normalized_job_key) > 160:
        raise CheckpointLifecycleError(
            "polling checkpoint job key exceeds 160 characters"
        )
    initial_cursor_was_supplied = not isinstance(
        initial_cursor, _InitialCursorUnspecified
    )
    requested_initial_cursor = (
        None if not initial_cursor_was_supplied else initial_cursor
    )
    if requested_initial_cursor is not None and not isinstance(
        requested_initial_cursor, str
    ):
        raise CheckpointLifecycleError(
            "polling checkpoint initial cursor must be a string or null"
        )

    _poll_binding(
        db,
        binding_id=capability_binding_id,
        registry=registry,
        require_enabled=False,
    )

    def existing_checkpoint() -> PollingCheckpoint | None:
        return cast(
            PollingCheckpoint | None,
            db.execute(
                select(PollingCheckpoint).where(
                    PollingCheckpoint.capability_binding_id == capability_binding_id,
                    PollingCheckpoint.job_key == normalized_job_key,
                )
            ).scalar_one_or_none(),
        )

    def replay(checkpoint: PollingCheckpoint) -> PollingCheckpointRegistration:
        if checkpoint.version != 1 or checkpoint.advanced_at is not None:
            if initial_cursor_was_supplied:
                raise CheckpointDefinitionConflict(
                    "polling checkpoint has advanced; its initial cursor is no "
                    "longer revalidatable, and rewind is not a lifecycle operation"
                )
        elif _cursor(checkpoint.cursor_json) != requested_initial_cursor:
            raise CheckpointDefinitionConflict(
                "polling checkpoint already has a different initial cursor; "
                "rewind and replacement are not lifecycle operations"
            )
        return PollingCheckpointRegistration(
            checkpoint=_checkpoint_ref(checkpoint), created=False
        )

    existing = existing_checkpoint()
    if existing is not None:
        return replay(existing)

    checkpoint = PollingCheckpoint(
        capability_binding_id=capability_binding_id,
        job_key=normalized_job_key,
        version=1,
        cursor_json={"cursor": requested_initial_cursor},
    )
    try:
        with conflict_savepoint(db):
            db.add(checkpoint)
            db.flush()
    except IntegrityError:
        winner = existing_checkpoint()
        if winner is None:
            # Under REPEATABLE READ the outer transaction may retain the
            # pre-race snapshot. Refuse with a material-safe typed outcome and
            # let the caller retry the command in a fresh unit of work.
            raise CheckpointCreationRaced(
                "a concurrent checkpoint declaration won; retry the same command"
            ) from None
        return replay(winner)
    return PollingCheckpointRegistration(
        checkpoint=_checkpoint_ref(checkpoint), created=True
    )


def enabled_polling_checkpoint_page(
    db: Any,
    *,
    limit: int,
    after: UUID | None = None,
) -> PollingCheckpointPage:
    """Return one stable page of enabled checkpoints as a scheduling hint.

    The selector owns no claim and no compatibility decision. A second worker
    may receive the same id, and :func:`poll_once` remains the final manifest,
    mode, configuration and optimistic-version gate. UUID keyset pagination is
    deliberate: a permanently stale/failing first checkpoint cannot occupy
    every bounded pass and starve the rest merely because its cursor never
    advances.
    """

    from sqlalchemy import select

    if limit < 1:
        raise ValueError("polling checkpoint limit must be positive")
    statement = (
        select(PollingCheckpoint.id)
        .join(
            CapabilityBinding,
            CapabilityBinding.id == PollingCheckpoint.capability_binding_id,
        )
        .join(
            ConnectorInstallation,
            ConnectorInstallation.id == CapabilityBinding.installation_id,
        )
        .where(
            CapabilityBinding.state == "enabled",
            ConnectorInstallation.state == "enabled",
        )
        .order_by(PollingCheckpoint.id)
        .limit(limit + 1)
    )
    if after is not None:
        statement = statement.where(PollingCheckpoint.id > after)
    selected = tuple(db.scalars(statement).all())
    checkpoint_ids = selected[:limit]
    return PollingCheckpointPage(
        checkpoint_ids=checkpoint_ids,
        next_after=checkpoint_ids[-1] if len(selected) > limit else None,
    )


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
    try:
        binding, installation = _poll_binding(
            db,
            binding_id=checkpoint.capability_binding_id,
            registry=registry,
            require_enabled=True,
        )
    except CheckpointLifecycleError as exc:
        raise PollUnavailable(str(exc)) from None

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
    *,
    registry: CapabilityRegistry | None = None,
) -> PollResult:
    """Record every event and advance the cursor in the same transaction.

    The observation gate ADR-0024 § 10.4.4 requires is NOT re-implemented here.
    `record_batch` owns it, and this path reaches it by handing over the same
    `prepared` value it already hands over — `PreparedPoll` satisfies
    `ReceiptBatchAddress`, `capability_id` included. A second copy of the check
    on this side would be a second answer to what an observation is, in the one
    module whose docstring says polling must not grow a second inbox.

    A refusal therefore lands BEFORE `advance_checkpoint`, which is the ordering
    that matters here: the cursor is a claim about durable receipts, and
    advancing it past events that were refused would permanently skip provider
    facts nothing ever recorded.
    """
    checkpoint = db.get(PollingCheckpoint, prepared.checkpoint_id)
    if checkpoint is None:
        raise PollUnavailable("polling checkpoint disappeared before settlement")

    recorded = record_batch(db, prepared, batch.events, registry=registry)
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
    capabilities: CapabilityRegistry | None = None,
) -> PollResult:
    """Run the three phases with no transaction spanning provider I/O.

    Two registries, deliberately two names. `registry` is the CONNECTOR registry
    — what is installed and executable. `capabilities` is the declared
    capability vocabulary — what the fleet's payloads MEAN. They are supplied by
    different parties (a deployment installs connectors; an owning application
    publishes contracts), and collapsing them into one argument would be the
    Integrator holding both, which is the ownership split ADR-0024 § 7 draws.
    """
    with unit_of_work() as db:
        prepared = prepare_poll(db, checkpoint_id=checkpoint_id, registry=registry)
    batch = invoke_poll(
        prepared,
        registry=registry,
        resolve_secrets=resolve_secrets,
    )
    with unit_of_work() as db:
        return record_poll_batch(db, prepared, batch, registry=capabilities)
