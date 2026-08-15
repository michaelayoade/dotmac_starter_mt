"""Document series and allocation receipts on explicit tenant and platform planes.

Four tables, two per plane. Both planes are DECLARED (ADR-0023) because both
exist: a tenant allocates its own invoice and credit-note series, and the
control plane allocates vendor-side series that no tenant may read. Neither is
inferred from the presence or absence of a tenant column.

Three rules hold across every table, and each answers a defect the source audit
found (`docs/inventories/numbering-source-variance.md`):

- **A series is explicitly configured; nothing is auto-created.** ERP's
  allocator invents a series on first use with a `DOC-` prefix from a default
  dictionary, so a typo silently becomes a live document series. There is no
  auto-create and no default-prefix table here: an unconfigured `series_code`
  fails closed.
- **Every allocation leaves an immutable receipt.** ERP's reset/update path
  rewrites counters with no evidence, which is how a committed number can be
  handed out twice. A receipt is written in the same transaction as the counter
  advance and is never updated — repair may advance the counter, never rewrite
  history.
- **The reset boundary is decided from a supplied business date.** ERP reads
  `date.today()` on the dominant caller path, so a backdated invoice takes this
  year's number. `reference_date` is a required column here precisely so a
  receipt records the date the decision was actually made against.

`series_code` is an open registered string, never an enum. ERP's `SequenceType`
is a 27-member PostgreSQL enum, which means a new document kind is a migration
in a shared module — exactly what ADR-0008 forbids.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

SCHEMA = module_schema("numbering")

#: Reset policies. An open string on the column, validated by the service
#: against this tuple — a product that needs `weekly` adds it here rather than
#: altering a PostgreSQL type in a shared schema.
RESET_POLICIES: tuple[str, ...] = ("never", "yearly", "monthly")


class _SeriesColumns:
    """Configuration shared by both planes.

    Every field is supplied by the installing product. The module reads no
    settings store and no clock: ADR-0009 keeps resolution off the network, and
    a series that could resolve its own configuration would be a second reader
    of the product's settings.
    """

    #: Open registered vocabulary. NOT an enum — see the module docstring.
    @declared_attr
    def series_code(cls) -> Mapped[str]:
        return mapped_column(String(80), nullable=False)

    @declared_attr
    def prefix(cls) -> Mapped[str]:
        return mapped_column(String(32), nullable=False, default="")

    @declared_attr
    def suffix(cls) -> Mapped[str]:
        return mapped_column(String(32), nullable=False, default="")

    @declared_attr
    def separator(cls) -> Mapped[str]:
        return mapped_column(String(8), nullable=False, default="-")

    @declared_attr
    def min_digits(cls) -> Mapped[int]:
        return mapped_column(Integer, nullable=False, default=6)

    @declared_attr
    def include_year(cls) -> Mapped[bool]:
        return mapped_column(Integer, nullable=False, default=0)

    @declared_attr
    def year_digits(cls) -> Mapped[int]:
        return mapped_column(Integer, nullable=False, default=4)

    @declared_attr
    def include_month(cls) -> Mapped[bool]:
        return mapped_column(Integer, nullable=False, default=0)

    @declared_attr
    def reset_policy(cls) -> Mapped[str]:
        return mapped_column(String(16), nullable=False, default="never")

    #: The next value to hand out. Advanced under a row lock, never rewound.
    @declared_attr
    def next_value(cls) -> Mapped[int]:
        return mapped_column(BigInteger, nullable=False, default=1)

    #: The period the counter currently belongs to, as `YYYY` or `YYYY-MM`.
    #: Compared by ORDERING, never equality — ERP compares inequality, so a
    #: backdated allocation rewinds the counter and reissues numbers.
    @declared_attr
    def current_period(cls) -> Mapped[str | None]:
        return mapped_column(String(7))


class _ReceiptColumns:
    """Immutable evidence of one allocation. Never updated, never deleted."""

    @declared_attr
    def series_code(cls) -> Mapped[str]:
        return mapped_column(String(80), nullable=False)

    @declared_attr
    def allocated_value(cls) -> Mapped[int]:
        return mapped_column(BigInteger, nullable=False)

    @declared_attr
    def formatted_number(cls) -> Mapped[str]:
        return mapped_column(String(255), nullable=False)

    #: The business date the caller supplied. Required: the reset decision is
    #: only auditable if the date it was made against is recorded.
    @declared_attr
    def reference_date(cls) -> Mapped[date]:
        return mapped_column(Date, nullable=False)

    @declared_attr
    def period(cls) -> Mapped[str | None]:
        return mapped_column(String(7))

    @declared_attr
    def idempotency_key(cls) -> Mapped[str]:
        return mapped_column(String(255), nullable=False)

    #: Digest of the allocation request. Its own column, per ADR-0014 — not
    #: packed into the key, where a truncated fingerprint silently collides.
    @declared_attr
    def request_fingerprint(cls) -> Mapped[str]:
        return mapped_column(String(64), nullable=False)

    @declared_attr
    def allocated_at(cls) -> Mapped[datetime]:
        return mapped_column(DateTime(timezone=True), nullable=False)

    @declared_attr
    def allocated_by(cls) -> Mapped[str | None]:
        return mapped_column(String(255))

    @declared_attr
    def note(cls) -> Mapped[str | None]:
        return mapped_column(Text)


# ── Tenant plane ────────────────────────────────────────────────────────────


class NumberSeries(Base, _SeriesColumns, TimestampMixin):
    """A tenant's configured document series."""

    __tablename__ = "number_series"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_number_series_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "series_code", name="uq_number_series_tenant_code"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class AllocationReceipt(Base, _ReceiptColumns, TimestampMixin):
    """Immutable evidence of one tenant allocation."""

    __tablename__ = "allocation_receipts"
    __table_args__ = (
        # The idempotency identity. A replay of the same key returns this row;
        # a different fingerprint under the same key is a conflict.
        UniqueConstraint(
            "tenant_id",
            "series_code",
            "idempotency_key",
            name="uq_allocation_receipts_identity",
        ),
        # A value is handed out once per series. This is the constraint that
        # makes a duplicate impossible rather than merely unlikely — Sub relies
        # on the consuming table's index and a retry loop instead.
        UniqueConstraint(
            "tenant_id",
            "series_code",
            "allocated_value",
            name="uq_allocation_receipts_value",
        ),
        Index("ix_allocation_receipts_tenant_series", "tenant_id", "series_code"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


# ── Platform plane ──────────────────────────────────────────────────────────
#
# No tenant column, no RLS, and REVOKEd from the tenant app role — that
# revocation IS the isolation here (ADR-0023). No foreign key crosses to the
# tenant plane, so neither plane can be read through the other.


class PlatformNumberSeries(Base, _SeriesColumns, TimestampMixin):
    """A control-plane configured series."""

    __tablename__ = "platform_number_series"
    __table_args__ = (
        UniqueConstraint(
            "series_code", name="uq_platform_number_series_code"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()


class PlatformAllocationReceipt(Base, _ReceiptColumns, TimestampMixin):
    """Immutable evidence of one control-plane allocation."""

    __tablename__ = "platform_allocation_receipts"
    __table_args__ = (
        UniqueConstraint(
            "series_code",
            "idempotency_key",
            name="uq_platform_allocation_receipts_identity",
        ),
        UniqueConstraint(
            "series_code",
            "allocated_value",
            name="uq_platform_allocation_receipts_value",
        ),
        Index("ix_platform_allocation_receipts_series", "series_code"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()


TENANT_TABLES: tuple[str, ...] = (
    "number_series",
    "allocation_receipts",
)
PLATFORM_TABLES: tuple[str, ...] = (
    "platform_number_series",
    "platform_allocation_receipts",
)

__all__ = [
    "PLATFORM_TABLES",
    "RESET_POLICIES",
    "SCHEMA",
    "TENANT_TABLES",
    "AllocationReceipt",
    "NumberSeries",
    "PlatformAllocationReceipt",
    "PlatformNumberSeries",
]
