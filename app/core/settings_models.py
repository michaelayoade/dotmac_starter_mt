"""Domain settings model — tenant-scoped configuration keyed by (domain, key).

Lives in `app.core` (not `app.features.settings`) even though it backs a
feature: the `custom_fields` feature (Task 4+) must consume the settings
*resolver* (also core, Task 4), and features may never import each other. The
settings FEATURE package (Tasks 4-6) owns only what nothing else needs — spec
declarations, seed data, router, schemas.

Tenancy is special for this table: `tenant_id` is NULLABLE. `tenant_id IS NULL`
means a platform-level default row, readable (but not writable) by every
tenant. See the RLS policies in the migration
(`alembic/versions/*_settings_table.py`) for the read/write split, and
`tests/test_settings_isolation.py` for the canary that proves it.

Ported from `dotmac_starter:app/models/domain_settings.py`, with:
- `tenant_id` added (ST/sub are single-tenant; here it's the platform-default
  discriminator described above).
- The value-alignment `CheckConstraint` restored from
  `dotmac_sub:app/models/domain_settings.py` (ST dropped it).
- `SettingDomain` trimmed to this app's four domains (ST has five, minus
  `scheduler`/`billing`, plus `custom_fields`).
"""

from __future__ import annotations

import enum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, uuid_pk


class SettingDomain(str, enum.Enum):
    auth = "auth"
    audit = "audit"
    branding = "branding"
    custom_fields = "custom_fields"


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
    domain: Mapped[SettingDomain] = mapped_column(
        sa.Enum(
            SettingDomain,
            name="ck_domain_settings_domain",
            native_enum=False,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
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
