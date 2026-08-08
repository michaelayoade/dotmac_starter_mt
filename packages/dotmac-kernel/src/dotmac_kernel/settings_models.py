"""Domain settings model — tenant-scoped configuration keyed by (domain, key).

Lives in `dotmac_kernel` rather than a feature package because the
`custom_fields` feature consumes the settings resolver directly and features
may never import each other. The `settings` FEATURE package owns only what
nothing else needs — spec declarations, seed data, router, schemas.

Tenancy is special for this table: `tenant_id` is NULLABLE. `tenant_id IS NULL`
means a platform-level default row, readable (but not writable) by every
tenant. See the RLS policies in the migration
for the read/write split, and `tests/test_settings_isolation.py` for the
canary that proves it.

Which domains exist is not fixed here — see `SettingDomain` below and
`dotmac_kernel.settings_resolver.SettingDomainRegistry`.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_kernel.models import Base, TimestampMixin, uuid_pk


class SettingDomain(str):
    """A setting domain — an open, registered string.

    A kernel cannot enumerate its consumers' domains: this repo has five,
    `dotmac_erp` has twenty-one and `dotmac_sub` twenty-eight, and
    the next product will have its own. Which domains are real is therefore a
    declaration — a module lists `setting_domains` on its manifest and
    `SettingDomainRegistry` validates writes against them, the same shape as
    the permission, capability, audit-action and flag catalogues.

    A `str` subclass rather than a bare alias, so a domain compares equal to
    its plain-string form in a query and `domain.value` reads the same as it
    does for `SettingValueType`. The kernel's own domains are bound as class
    attributes below; a product constructs its own — `SettingDomain("payroll")`.
    """

    __slots__ = ()

    # Bound below; declared here for the type-checker.
    auth: ClassVar[SettingDomain]
    audit: ClassVar[SettingDomain]
    branding: ClassVar[SettingDomain]
    custom_fields: ClassVar[SettingDomain]
    display: ClassVar[SettingDomain]

    @property
    def value(self) -> str:
        return str(self)

    def __repr__(self) -> str:
        return f"SettingDomain({str(self)!r})"


# The kernel's own domains, backing kernel-owned surfaces (branding, display)
# and this assembly's features. A product registers its own alongside these
# rather than editing this tuple.
KERNEL_SETTING_DOMAINS: tuple[SettingDomain, ...] = tuple(
    SettingDomain(name)
    for name in ("auth", "audit", "branding", "custom_fields", "display")
)
for _domain in KERNEL_SETTING_DOMAINS:
    setattr(SettingDomain, str(_domain), _domain)
del _domain


class SettingValueType(str, enum.Enum):
    string = "string"
    integer = "integer"
    boolean = "boolean"
    json = "json"


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_cls]


class DomainSetting(Base, TimestampMixin):
    """A single setting row, either tenant-owned or a platform default.

    Uniqueness can't be one composite `UniqueConstraint` because Postgres
    unique constraints treat NULL as distinct from every other NULL — any
    number of `tenant_id IS NULL` rows could collide on `(domain, key)`
    without an explicit partial index. The migration creates two partial
    unique indexes instead (mirrored here for ORM-level parity):
    `uq_domain_settings_platform` (tenant_id IS NULL) and
    `uq_domain_settings_tenant` (tenant_id IS NOT NULL).
    """

    __tablename__ = "domain_settings"
    __table_args__ = (
        CheckConstraint(
            "(value_type = 'json' AND value_json IS NOT NULL AND value_text IS NULL) "
            "OR (value_type != 'json' AND value_text IS NOT NULL)",
            name="ck_domain_settings_value_alignment",
        ),
        Index(
            "uq_domain_settings_platform",
            "domain",
            "key",
            unique=True,
            postgresql_where=sa.text("tenant_id IS NULL"),
        ),
        Index(
            "uq_domain_settings_tenant",
            "tenant_id",
            "domain",
            "key",
            unique=True,
            postgresql_where=sa.text("tenant_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # String, not a database enum: a CHECK constraint over a fixed member list
    # would need a kernel migration per consuming product. The domain is
    # validated at the write boundary against `SettingDomainRegistry`.
    domain: Mapped[SettingDomain] = mapped_column(String(120), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value_type: Mapped[SettingValueType] = mapped_column(
        sa.Enum(
            SettingValueType,
            name="ck_domain_settings_value_type",
            native_enum=False,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=SettingValueType.string,
    )
    value_text: Mapped[str | None] = mapped_column(Text)
    value_json: Mapped[dict | None] = mapped_column(
        sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    )
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SettingChangeAction(str, enum.Enum):
    create = "create"
    update = "update"
    delete = "delete"


class DomainSettingHistory(Base):
    """One recorded transition of a setting's value.

    Answers "what was this before, and when did it change" — a question the
    audit trail cannot answer, because `AuditEvent` records that a change
    happened, not what the value was.

    **The two are deliberately split, and this table does NOT record the actor.**
    `dotmac_kernel.audit.write_audit_event` is the one writer of who-did-what;
    duplicating actor, IP and user-agent here would make a second authority for
    the same fact, and the two would drift. The pair correlates on
    `(tenant_id, domain, key)` and adjacent timestamps: the audit event says who
    changed a setting, this row says what it became.

    **A secret's value is never recorded.** `value_before`/`value_after` are
    NULL when the spec is secret, and `secret_changed` carries the fact that it
    moved. Keeping past credentials in a history table with its own retention
    would mean that rotating a compromised secret leaves the compromised one
    readable forever — the table meant to explain a change would become the
    place a leak persists.

    Tenancy mirrors `domain_settings` exactly, including its documented
    exception to the tenant_id-NOT-NULL rule: `NULL` is a platform-scope change
    every tenant may read and none may write. See the migration for the RLS
    split, identical to the parent table's.
    """

    __tablename__ = "domain_setting_history"
    __table_args__ = (
        Index("ix_domain_setting_history_lookup", "tenant_id", "domain", "key"),
        Index("ix_domain_setting_history_changed_at", "changed_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    # Denormalised from the parent so history survives the setting's deletion —
    # the transition that matters most is often the last one.
    tenant_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True, index=True)
    domain: Mapped[SettingDomain] = mapped_column(String(120), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    setting_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey("domain_settings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[SettingChangeAction] = mapped_column(
        sa.Enum(
            SettingChangeAction,
            name="ck_domain_setting_history_action",
            native_enum=False,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    # Rendered as text for both scalar and json settings: history is read by a
    # human comparing two states, not by code re-parsing them.
    value_before: Mapped[str | None] = mapped_column(Text)
    value_after: Mapped[str | None] = mapped_column(Text)
    # True when the setting is secret, in which case the two columns above stay
    # NULL — see the class docstring.
    secret_changed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


__all__ = [
    "KERNEL_SETTING_DOMAINS",
    "DomainSetting",
    "DomainSettingHistory",
    "SettingChangeAction",
    "SettingDomain",
    "SettingValueType",
]
