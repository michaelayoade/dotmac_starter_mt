"""Operational escalation persistence contract.

Sub's `operational_escalation_policies` is a MUTABLE row: editing it silently
rewrites the terms every already-open escalation was raised under, and there is
no way to read back what the policy said at the time. Here a policy is a stable
identity plus IMMUTABLE versions, and an instance binds the exact version it was
raised under — so an escalation stays auditable after the policy moves on.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, cast
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
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_operational_escalations.contracts import (
    EscalationStatus,
    PolicyVersionState,
)

SCHEMA = module_schema("escalations")


def _enum(python_type: type[enum.StrEnum], name: str) -> sa.Enum:
    return sa.Enum(
        python_type,
        name=name,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
        create_constraint=True,
    )


class EscalationPolicy(Base, TimestampMixin):
    __tablename__ = "escalation_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_escalation_policies_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "code", name="uq_escalation_policies_tenant_code"
        ),
        Index(
            "ix_escalation_policies_tenant_subject_trigger",
            "tenant_id",
            "subject_type",
            "trigger",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False)
    trigger: Mapped[str] = mapped_column(String(120), nullable=False)


class EscalationPolicyVersion(Base, TimestampMixin):
    __tablename__ = "escalation_policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_escalation_policy_versions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "policy_id",
            "version",
            name="uq_escalation_policy_versions_tenant_policy_version",
        ),
        CheckConstraint("version >= 1", name="ck_escalation_policy_versions_version"),
        CheckConstraint("level >= 1", name="ck_escalation_policy_versions_level"),
        CheckConstraint(
            "cooldown_seconds >= 0", name="ck_escalation_policy_versions_cooldown"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                f"{SCHEMA}.escalation_policies.tenant_id",
                f"{SCHEMA}.escalation_policies.id",
            ],
            ondelete="CASCADE",
            name="fk_escalation_policy_versions_tenant_policy",
        ),
        Index(
            "ix_escalation_policy_versions_tenant_policy_state",
            "tenant_id",
            "policy_id",
            "state",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    policy_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    channels: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    minimum_severity: Mapped[str | None] = mapped_column(String(40), nullable=True)
    unowned_after_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unresolved_after_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[PolicyVersionState] = mapped_column(
        _enum(PolicyVersionState, "escalation_policy_version_state"), nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EscalationInstance(Base, TimestampMixin):
    __tablename__ = "escalation_instances"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_escalation_instances_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "dedup_key", name="uq_escalation_instances_tenant_dedup_key"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_version_id"],
            [
                f"{SCHEMA}.escalation_policy_versions.tenant_id",
                f"{SCHEMA}.escalation_policy_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_escalation_instances_tenant_policy_version",
        ),
        Index(
            "ix_escalation_instances_tenant_subject_status",
            "tenant_id",
            "subject_reference",
            "status",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    policy_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    trigger: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str | None] = mapped_column(String(40), nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[EscalationStatus] = mapped_column(
        _enum(EscalationStatus, "escalation_status"), nullable=False
    )
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_by_reference: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    settlement_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class PolicyVersionImmutableError(RuntimeError):
    """Raised when a published escalation policy version's terms are rewritten."""


_MUTABLE_VERSION_FIELDS = frozenset({"state", "activated_at", "retired_at"})


@event.listens_for(EscalationPolicyVersion, "before_update")
def _reject_published_version_rewrite(
    _mapper: object, _connection: object, target: EscalationPolicyVersion
) -> None:
    """Lifecycle may move; terms may not.

    Only `state`/`activated_at`/`retired_at` are writable after the row exists.
    A change to a level, channel set, threshold or cooldown must be a NEW
    version, or every open escalation silently changes meaning.
    """
    state = sa.inspect(target)
    changed = {
        attribute.key
        for attribute in state.attrs
        if attribute.history.has_changes() and attribute.key != "updated_at"
    }
    if changed - _MUTABLE_VERSION_FIELDS:
        raise PolicyVersionImmutableError(
            "escalation policy version terms are immutable; publish a new version "
            f"(attempted to change {sorted(changed - _MUTABLE_VERSION_FIELDS)})"
        )


TENANT_TABLES = (
    "escalation_policies",
    "escalation_policy_versions",
    "escalation_instances",
)
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (EscalationPolicy, EscalationPolicyVersion, EscalationInstance)
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "EscalationInstance",
    "EscalationPolicy",
    "EscalationPolicyVersion",
    "PolicyVersionImmutableError",
    "metadata_table",
]
