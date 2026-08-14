"""The control-plane tables, bound to `mod_intg` (ADR-0006 D1, ADR-0023).

## Platform plane only — and why that is a port, not a re-scoping

Every table here is PLATFORM-plane: no `tenant_id`, no RLS, granted to the
platform roles and REVOKEd from `app_user`. A connector installation, its
configuration revisions and its capability bindings are control-plane facts
about the fleet's integrations. No product queries them — products receive
provider-neutral capability messages over their own ports — and none of these
rows belongs to a tenant of a product data plane.

The source agrees. Not one of `dotmac_sub`'s seven integration tables carries a
`tenant_id`: Sub runs one operator tenant and built this control-plane-scoped
from the start. So the manifest declares `platform_tables` with an EMPTY tenant
`tables` tuple — the first module in the fleet to do so — and that is a
faithful port rather than a decision imposed on the source.

## Binding multiplicity: enabled is not selected

`(installation_id, capability_id)` is unique — an installation binds a
capability once. **`capability_id` alone is deliberately NOT unique**: many
installations may implement one capability, which is what ADR-0024 § 7's
"each `(installation, capability)`" actually says.

Choosing between them is a DISPATCH concern, not a schema one. See
`dotmac_integration.selection`: exactly one enabled binding is resolved per
dispatch, and absent, stale or ambiguous routing fails closed.

`scope_json` appears on the binding for parity with the source, where its only
consumer displays configured domains. It is **not** in any uniqueness
constraint and must not be: JSON equality cannot detect overlapping scopes, so
a constraint over it would claim a guarantee it cannot make. Scoped routing, if
it ever gains a real consumer, needs a typed route contract with canonical keys
and overlap rules.

## Configuration revisions are immutable

A revision is inserted, never updated. `config_digest` is what makes that
checkable rather than asserted, and `current_config_revision_id` on the
installation is the only mutable pointer — so "what was this connector
configured with on the 3rd?" is answerable, which is the question an incident
asks.

`secret_refs` holds REFERENCES only. See `dotmac_integration.secret_refs` for the
refusal that keeps it that way.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

#: The module's immutable namespace, from the ledger allocation.
SCHEMA = module_schema("intg")

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

#: An installation's lifecycle. `quarantined` is distinct from `disabled`: an
#: operator disabled the first, the platform stopped trusting the second.
INSTALLATION_STATES: tuple[str, ...] = (
    "draft",
    "validating",
    "enabled",
    "disabled",
    "quarantined",
    "retired",
)
BINDING_STATES: tuple[str, ...] = ("disabled", "enabled")


class ConnectorInstallation(Base, TimestampMixin):
    """One configured instance of an installed connector distribution."""

    __tablename__ = "connector_installations"
    __table_args__ = (
        UniqueConstraint(
            "connector_key", "name", name="uq_connector_installations_key_name"
        ),
        CheckConstraint(
            "state IN ('draft', 'validating', 'enabled', 'disabled', "
            "'quarantined', 'retired')",
            name="ck_connector_installations_state",
        ),
        CheckConstraint(
            "environment IN ('production', 'sandbox', 'test')",
            name="ck_connector_installations_environment",
        ),
        Index("ix_connector_installations_key_state", "connector_key", "state"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    #: Matches a discovered `ConnectorManifest.connector_key`. Not an FK —
    #: connectors are independently released distributions, not rows.
    connector_key: Mapped[str] = mapped_column(String(120), nullable=False)
    connector_version: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The SPI range the installed distribution declared, STORED rather than
    #: only compared. This is what lets a later module upgrade refuse a
    #: previously activated binding — the case that actually bites, where the
    #: plugin did not change but the host did.
    spi_range: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    environment: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="production"
    )
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="draft"
    )
    state_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The only mutable pointer into the immutable revision history. A REAL
    #: foreign key, `use_alter` because the two tables reference each other:
    #: without it a deleted revision leaves an installation pointing at nothing
    #: and "what is this connector configured with?" has no answer.
    current_config_revision_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey(
            f"{SCHEMA}.connector_config_revisions.id",
            name="fk_connector_installations_current_revision",
            use_alter=True,
        ),
        nullable=True,
    )

    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Actor identifiers, deliberately not FKs: this module is composed by a
    #: control-plane assembly whose identity model it must not presume.
    created_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(160), nullable=True)


class ConnectorConfigRevision(Base):
    """An immutable configuration revision. Inserted, never updated."""

    __tablename__ = "connector_config_revisions"
    __table_args__ = (
        UniqueConstraint(
            "installation_id", "revision", name="uq_connector_config_revisions_number"
        ),
        # Restored from the source. Re-submitting an IDENTICAL configuration
        # must not mint a new revision — otherwise every reconcile inflates the
        # history and "when did this last change?" stops being answerable.
        UniqueConstraint(
            "installation_id",
            "config_digest",
            name="uq_connector_config_revisions_digest",
        ),
        CheckConstraint(
            "validation_status IN ('pending', 'valid', 'invalid')",
            name="ck_connector_config_revisions_validation",
        ),
        CheckConstraint("revision >= 1", name="ck_connector_config_revisions_revision"),
        Index(
            "ix_connector_config_revisions_installation",
            "installation_id",
            "revision",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    installation_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.connector_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)

    config_json: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    #: Secret REFERENCES only — never values. `dotmac_integration.secret_refs`
    #: refuses anything that looks like material rather than a pointer.
    secret_refs: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    #: Over config + refs. Immutability is checkable rather than asserted.
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)

    validation_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="pending"
    )
    validation_errors: Mapped[list[object] | None] = mapped_column(_JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class CapabilityBinding(Base, TimestampMixin):
    """This installation implements this capability.

    ENABLED, not SELECTED. Being enabled means capable and permitted; it does
    not mean chosen. Many installations may be enabled for one capability, and
    picking one is `dotmac_integration.selection`'s job.
    """

    __tablename__ = "capability_bindings"
    __table_args__ = (
        # The ADR-0024 § 7 tuple: an installation binds a capability ONCE.
        UniqueConstraint(
            "installation_id",
            "capability_id",
            name="uq_capability_bindings_installation_capability",
        ),
        CheckConstraint(
            "state IN ('disabled', 'enabled')", name="ck_capability_bindings_state"
        ),
        # Supports the selection query; deliberately NOT unique — see the module
        # docstring.
        Index("ix_capability_bindings_capability_state", "capability_id", "state"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    installation_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.connector_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: A capability CONTRACT id, e.g. `ticket.observation.v1`. Must be declared
    #: by the installation's connector manifest — see `activation`.
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="disabled"
    )

    #: Parity with the source, where its only consumer DISPLAYS configured
    #: domains. Never in a uniqueness constraint and never read by routing.
    scope_json: Mapped[dict[str, object] | None] = mapped_column(_JSON, nullable=True)
    #: Selection policy. `{"default": true}` marks the binding chosen when a
    #: dispatch does not name one and several are enabled.
    policy_json: Mapped[dict[str, object] | None] = mapped_column(_JSON, nullable=True)

    enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(160), nullable=True)


#: This module owns no tenant-plane table. Declared as an explicit empty tuple
#: rather than omitted, so "tenant-plane: none" is a statement in the manifest
#: rather than an absence a reader has to infer (ADR-0023).
TENANT_TABLES: tuple[str, ...] = ()
PLATFORM_TABLES: tuple[str, ...] = (
    "connector_installations",
    "connector_config_revisions",
    "capability_bindings",
    "event_subscriptions",
    "inbox_receipts",
    "delivery_attempts",
    "polling_checkpoints",
)

__all__ = [
    "BINDING_STATES",
    "INSTALLATION_STATES",
    "PLATFORM_TABLES",
    "SCHEMA",
    "TENANT_TABLES",
    "CapabilityBinding",
    "ConnectorConfigRevision",
    "ConnectorInstallation",
    "DeliveryAttempt",
    "EventSubscription",
    "InboxReceipt",
    "PollingCheckpoint",
]


class EventSubscription(Base, TimestampMixin):
    """Which event types a binding wants delivered.

    The seventh table, and the one the first port dropped. Without it the outbox
    has no declaration of WHAT to deliver: every enqueue would be an implicit
    subscription decided by whatever called it, and turning a noisy event type
    off would mean changing code rather than configuration.

    `(capability_binding_id, event_type)` is unique — a binding subscribes to an
    event type once. `filter_json` narrows within a type; `payload_policy_json`
    says how much of the payload may cross the boundary, which is where a
    product limits what an external system is told.
    """

    __tablename__ = "event_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "capability_binding_id",
            "event_type",
            name="uq_event_subscriptions_binding_event",
        ),
        CheckConstraint(
            "state IN ('disabled', 'enabled')", name="ck_event_subscriptions_state"
        ),
        Index("ix_event_subscriptions_event_state", "event_type", "state"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    capability_binding_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.capability_bindings.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="disabled"
    )
    #: Narrows within an event type. Structural filtering only — a filter that
    #: encoded a business rule would put product policy in the control plane.
    filter_json: Mapped[dict[str, object] | None] = mapped_column(_JSON, nullable=True)
    #: How much of the payload may leave. This is where a product limits what an
    #: external system is told about its domain.
    payload_policy_json: Mapped[dict[str, object] | None] = mapped_column(
        _JSON, nullable=True
    )

    created_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(160), nullable=True)


# ── Execution machinery (slice 2) ───────────────────────────────────────────


class InboxReceipt(Base):
    """A verified inbound provider event, recorded once.

    `(installation_id, provider_event_id)` is the DEDUPLICATION key, and it is a
    database constraint rather than a service check because two webhook workers
    racing the same redelivery is the normal case, not the edge case.

    `payload_digest` makes the stronger statement: the same event id arriving
    with DIFFERENT content is a provider identity collision, and the service
    raises rather than silently treating it as a duplicate. Deduping it would
    discard real content on the assumption the provider is well-behaved.
    """

    __tablename__ = "inbox_receipts"
    __table_args__ = (
        # BINDING-scoped, as in the source. The binding is what determines
        # which capability handles an event, so two bindings legitimately
        # observing one upstream event are two receipts with two consequences —
        # deduplicating them at the installation would silently drop one.
        UniqueConstraint(
            "capability_binding_id",
            "provider_event_id",
            name="uq_inbox_receipts_binding_event",
        ),
        CheckConstraint(
            "state IN ('verified', 'processing', 'processed', 'retryable', "
            "'reconciliation_required', 'dead_letter')",
            name="ck_inbox_receipts_state",
        ),
        Index("ix_inbox_receipts_state_received", "state", "received_at"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    installation_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.connector_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: NOT NULL, as in the source: an inbound event that is not routed to a
    #: capability has nothing that could process it, and admitting one would
    #: create a receipt no consequence can ever be attached to.
    capability_binding_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.capability_bindings.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider_event_id: Mapped[str] = mapped_column(String(240), nullable=False)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, object] | None] = mapped_column(_JSON, nullable=True)
    headers_json: Mapped[dict[str, object] | None] = mapped_column(_JSON, nullable=True)

    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="verified"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    #: What the receipt CAUSED, recorded so a replay can be compared against it
    #: rather than guessed at.
    consequence_json: Mapped[dict[str, object] | None] = mapped_column(
        _JSON, nullable=True
    )
    #: A CONNECTOR's vocabulary. Stored, never branched on — see `retry`.
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DeliveryAttempt(Base):
    """One outbound delivery and its retry state — the outbox.

    `idempotency_key` is unique per installation: the same logical effect
    enqueued twice is one row, which is what makes "at most once" a property of
    the QUEUE rather than a hope about callers.

    Execution at-most-once is NOT owned here. `dotmac_integration.execution`
    adapts `dotmac_kernel.idempotency.execute_once_platform`, because ADR-0014
    gives that exactly one owner in the fleet and a second ledger beside it is
    the failure that ADR records.
    """

    __tablename__ = "delivery_attempts"
    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "idempotency_key",
            name="uq_delivery_attempts_installation_key",
        ),
        CheckConstraint(
            "state IN ('pending', 'in_flight', 'delivered', 'retryable', "
            "'reconciliation_required', 'dead_letter')",
            name="ck_delivery_attempts_state",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_delivery_attempts_attempts"),
        # The dispatcher's query: what is due, oldest first.
        Index("ix_delivery_attempts_state_next", "state", "next_attempt_at"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    installation_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.connector_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    capability_binding_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)

    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, object] | None] = mapped_column(_JSON, nullable=True)

    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: A worker's claim. Without it two dispatchers deliver the same row twice,
    #: which no downstream idempotency can fully repair once a provider has
    #: seen both.
    leased_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PollingCheckpoint(Base):
    """Where a polling job got to, with an OPTIMISTIC LOCK.

    `version` is the whole point. Two workers advancing one cursor without it
    silently lose events: the slower write wins and the window between the two
    cursors is never polled again. A conditional update on `version` turns that
    into a refusal the caller can retry.
    """

    __tablename__ = "polling_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "capability_binding_id",
            "job_key",
            name="uq_polling_checkpoints_binding_job",
        ),
        CheckConstraint("version >= 1", name="ck_polling_checkpoints_version"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    capability_binding_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.capability_bindings.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Which poll this is. A binding may run several (backfill vs live tail).
    job_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    cursor_json: Mapped[dict[str, object] | None] = mapped_column(_JSON, nullable=True)
    advanced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
