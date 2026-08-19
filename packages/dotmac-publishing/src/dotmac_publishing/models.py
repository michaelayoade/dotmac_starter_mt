"""Four-table tenant publication persistence contract."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dotmac_publishing.contracts import DeliveryOutcome
from dotmac_publishing.lifecycle import DeliveryState, PublicationState

SCHEMA = module_schema("publishing")


def _state_column(enum_type: type[enum.Enum], name: str) -> sa.Enum:
    return sa.Enum(
        enum_type,
        name=name,
        native_enum=False,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
        create_constraint=True,
    )


class PublicationRelease(Base, TimestampMixin):
    __tablename__ = "publication_releases"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_publication_releases_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "request_key",
            name="uq_publication_releases_tenant_request_key",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_publication_releases_request_fingerprint",
        ),
        CheckConstraint(
            "length(snapshot_digest) = 64",
            name="ck_publication_releases_snapshot_digest",
        ),
        Index(
            "ix_publication_releases_tenant_state",
            "tenant_id",
            "state",
        ),
        Index(
            "ix_publication_releases_tenant_requested",
            "tenant_id",
            "requested_for",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    request_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    snapshot_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    snapshot_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[PublicationState] = mapped_column(
        _state_column(PublicationState, "publication_state"),
        nullable=False,
        default=PublicationState.SCHEDULED,
        server_default=PublicationState.SCHEDULED.value,
    )
    timer_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)

    deliveries: Mapped[list[PublicationDelivery]] = relationship(
        back_populates="release",
        cascade="all, delete-orphan",
        order_by="PublicationDelivery.target_order, PublicationDelivery.id",
    )


class PublicationDelivery(Base, TimestampMixin):
    __tablename__ = "publication_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_publication_deliveries_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "publication_release_id",
            "target_ref",
            name="uq_publication_deliveries_tenant_release_target",
        ),
        UniqueConstraint(
            "tenant_id",
            "publication_release_id",
            "target_order",
            name="uq_publication_deliveries_tenant_release_order",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "publication_release_id"],
            [
                f"{SCHEMA}.publication_releases.tenant_id",
                f"{SCHEMA}.publication_releases.id",
            ],
            ondelete="CASCADE",
            name="fk_publication_deliveries_tenant_release",
        ),
        Index(
            "ix_publication_deliveries_tenant_release",
            "tenant_id",
            "publication_release_id",
        ),
        Index(
            "ix_publication_deliveries_tenant_state",
            "tenant_id",
            "state",
        ),
        CheckConstraint(
            "target_order >= 0", name="ck_publication_deliveries_target_order"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    publication_release_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    target_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    variant_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[DeliveryState] = mapped_column(
        _state_column(DeliveryState, "publication_delivery_state"),
        nullable=False,
        default=DeliveryState.PENDING,
        server_default=DeliveryState.PENDING.value,
    )
    remote_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    release: Mapped[PublicationRelease] = relationship(back_populates="deliveries")
    attempts: Mapped[list[PublicationAttempt]] = relationship(
        back_populates="delivery",
        cascade="all, delete-orphan",
        order_by="PublicationAttempt.attempt_number, PublicationAttempt.id",
    )


class PublicationAttempt(Base, TimestampMixin):
    __tablename__ = "publication_attempts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_publication_attempts_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "publication_delivery_id",
            "attempt_number",
            name="uq_publication_attempts_tenant_delivery_number",
        ),
        UniqueConstraint(
            "tenant_id",
            "outbox_event_ref",
            name="uq_publication_attempts_tenant_outbox_ref",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "publication_delivery_id"],
            [
                f"{SCHEMA}.publication_deliveries.tenant_id",
                f"{SCHEMA}.publication_deliveries.id",
            ],
            ondelete="CASCADE",
            name="fk_publication_attempts_tenant_delivery",
        ),
        CheckConstraint(
            "attempt_number > 0", name="ck_publication_attempts_positive_number"
        ),
        Index(
            "ix_publication_attempts_tenant_delivery",
            "tenant_id",
            "publication_delivery_id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    publication_delivery_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[DeliveryState] = mapped_column(
        _state_column(DeliveryState, "publication_attempt_state"),
        nullable=False,
    )
    outbox_event_ref: Mapped[str] = mapped_column(String(36), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    delivery: Mapped[PublicationDelivery] = relationship(back_populates="attempts")
    observations: Mapped[list[PublicationObservation]] = relationship(
        back_populates="attempt",
        cascade="all, delete-orphan",
        order_by="PublicationObservation.observed_at, PublicationObservation.id",
    )


class PublicationObservation(Base, TimestampMixin):
    __tablename__ = "publication_observations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_publication_observations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "receipt_ref",
            name="uq_publication_observations_tenant_receipt_ref",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "publication_attempt_id"],
            [
                f"{SCHEMA}.publication_attempts.tenant_id",
                f"{SCHEMA}.publication_attempts.id",
            ],
            ondelete="CASCADE",
            name="fk_publication_observations_tenant_attempt",
        ),
        CheckConstraint(
            "length(fingerprint) = 64",
            name="ck_publication_observations_fingerprint",
        ),
        Index(
            "ix_publication_observations_tenant_attempt",
            "tenant_id",
            "publication_attempt_id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    publication_attempt_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    receipt_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[DeliveryOutcome] = mapped_column(
        _state_column(DeliveryOutcome, "publication_delivery_outcome"),
        nullable=False,
    )
    remote_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    attempt: Mapped[PublicationAttempt] = relationship(back_populates="observations")


PUBLISHING_MODELS = (
    PublicationRelease,
    PublicationDelivery,
    PublicationAttempt,
    PublicationObservation,
)

TENANT_TABLES: tuple[str, ...] = tuple(
    model.__tablename__ for model in PUBLISHING_MODELS
)

_TABLE_BY_NAME: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__) for model in PUBLISHING_MODELS
}


def metadata_table(table_name: str) -> sa.Table:
    """Return one declared table for assembly and catalogue gates."""
    return _TABLE_BY_NAME[table_name]


__all__ = [
    "PUBLISHING_MODELS",
    "SCHEMA",
    "TENANT_TABLES",
    "PublicationAttempt",
    "PublicationDelivery",
    "PublicationObservation",
    "PublicationRelease",
    "metadata_table",
]
