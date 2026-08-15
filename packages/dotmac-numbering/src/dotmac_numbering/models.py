"""Series configuration, per-period counters, receipts and repair evidence.

Eight tables, four per plane. Both planes are DECLARED (ADR-0023): a tenant
allocates its own document series, and the control plane allocates vendor-side
series no tenant may read.

## Why the counter is per PERIOD, not per series

A resetting series does not have one counter — it has one per period, and
collapsing them is unsound in two separate ways.

The obvious way: with a single counter, yearly reset hands out `1` again in
2027, so a receipt unique on `(scope, series, value)` collides with the 2026
row that already holds `1`.

The subtle way, which survives naively adding the period to that unique key: a
counter that has already advanced into 2027 still answers a *backdated* 2026
allocation. The number is formatted `…-2026-…` from the 2027 counter, so it can
collide with a 2026 number already issued — and no constraint keyed on the
counter's own period would see it, because the row says 2027 while the printed
number says 2026.

One counter per `(scope, series_code, period_key)` removes both. A 2026
allocation reads the 2026 counter whenever it arrives, and the receipt's
`period_key` is the period the NUMBER belongs to, not the period the clock was
in.

`period_key` is `NOT NULL` on every table. A non-resetting series uses the
literal `*`, so there is no nullable column to reason about and every unique
key is total.

## Why receipts are structurally immutable

`updated_at` is deliberately absent: a column that records a change to an
immutable row is either dead or a lie. The migration additionally grants only
`SELECT, INSERT` to the online roles and installs a trigger that refuses
`UPDATE` and `DELETE`, so immutability is a property of the database rather
than a convention the service is trusted to keep.
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

#: Reset policies, validated by the service. An open tuple rather than a
#: PostgreSQL enum: a product needing `weekly` adds it here, not in a migration
#: against a shared schema (ADR-0008).
RESET_POLICIES: tuple[str, ...] = ("never", "yearly", "monthly")

#: The period key of a series that never resets. A literal, so `period_key` is
#: NOT NULL everywhere and no unique constraint has a nullable member.
NO_PERIOD: str = "*"


class _SeriesColumns:
    """Configuration. Supplied by the installing product, never resolved here.

    Note what is absent: `next_value` and `current_period`. Counter state lives
    on the per-period counter row, so reconfiguring a series cannot move a
    counter and advancing a counter cannot reconfigure a series.
    """

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
    def include_year(cls) -> Mapped[int]:
        return mapped_column(Integer, nullable=False, default=0)

    @declared_attr
    def year_digits(cls) -> Mapped[int]:
        return mapped_column(Integer, nullable=False, default=4)

    @declared_attr
    def include_month(cls) -> Mapped[int]:
        return mapped_column(Integer, nullable=False, default=0)

    @declared_attr
    def reset_policy(cls) -> Mapped[str]:
        return mapped_column(String(16), nullable=False, default="never")

    @declared_attr
    def start_value(cls) -> Mapped[int]:
        return mapped_column(BigInteger, nullable=False, default=1)


class _CounterColumns:
    """One counter per period. The only mutable state in the module."""

    @declared_attr
    def series_code(cls) -> Mapped[str]:
        return mapped_column(String(80), nullable=False)

    @declared_attr
    def period_key(cls) -> Mapped[str]:
        return mapped_column(String(7), nullable=False)

    @declared_attr
    def next_value(cls) -> Mapped[int]:
        return mapped_column(BigInteger, nullable=False)


class _ReceiptColumns:
    """Immutable evidence of one allocation. No `updated_at` by design."""

    @declared_attr
    def series_code(cls) -> Mapped[str]:
        return mapped_column(String(80), nullable=False)

    #: The period the NUMBER belongs to — derived from `reference_date`, never
    #: from the counter's own state.
    @declared_attr
    def period_key(cls) -> Mapped[str]:
        return mapped_column(String(7), nullable=False)

    @declared_attr
    def allocated_value(cls) -> Mapped[int]:
        return mapped_column(BigInteger, nullable=False)

    @declared_attr
    def formatted_number(cls) -> Mapped[str]:
        return mapped_column(String(255), nullable=False)

    @declared_attr
    def reference_date(cls) -> Mapped[date]:
        return mapped_column(Date, nullable=False)

    #: Carried for operator traceability only. Replay and conflict are the
    #: kernel ledger's job (hard rule 23); this is domain evidence, not a
    #: second idempotency mechanism.
    @declared_attr
    def idempotency_key(cls) -> Mapped[str]:
        return mapped_column(String(255), nullable=False)

    @declared_attr
    def allocated_at(cls) -> Mapped[datetime]:
        return mapped_column(DateTime(timezone=True), nullable=False)

    @declared_attr
    def allocated_by(cls) -> Mapped[str | None]:
        return mapped_column(String(255))


class _RepairColumns:
    """Immutable evidence of one counter repair.

    A repair moves a number that will be printed on a document, so it is a
    decision and needs the same durability as an allocation: which period, from
    what, to what, on whose authority and why.
    """

    @declared_attr
    def series_code(cls) -> Mapped[str]:
        return mapped_column(String(80), nullable=False)

    @declared_attr
    def period_key(cls) -> Mapped[str]:
        return mapped_column(String(7), nullable=False)

    @declared_attr
    def previous_next_value(cls) -> Mapped[int]:
        return mapped_column(BigInteger, nullable=False)

    @declared_attr
    def new_next_value(cls) -> Mapped[int]:
        return mapped_column(BigInteger, nullable=False)

    @declared_attr
    def proven_minimum(cls) -> Mapped[int]:
        return mapped_column(BigInteger, nullable=False)

    @declared_attr
    def reason(cls) -> Mapped[str]:
        return mapped_column(Text, nullable=False)

    @declared_attr
    def repaired_by(cls) -> Mapped[str]:
        return mapped_column(String(255), nullable=False)

    @declared_attr
    def repaired_at(cls) -> Mapped[datetime]:
        return mapped_column(DateTime(timezone=True), nullable=False)


# ── Tenant plane ────────────────────────────────────────────────────────────


class NumberSeries(Base, _SeriesColumns, TimestampMixin):
    """A tenant's configured series. Configuration only — no counter state."""

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


class SeriesCounter(Base, _CounterColumns, TimestampMixin):
    """One counter per (tenant, series, period)."""

    __tablename__ = "series_counters"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "series_code",
            "period_key",
            name="uq_series_counters_identity",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class AllocationReceipt(Base, _ReceiptColumns):
    """Immutable evidence of one tenant allocation.

    Not `TimestampMixin`: `allocated_at` is the instant, and an `updated_at` on
    a row that cannot be updated is either dead or a lie.
    """

    __tablename__ = "allocation_receipts"
    __table_args__ = (
        # A value is handed out once per (series, PERIOD) — the period is part
        # of the identity because a resetting series legitimately reuses values
        # across periods.
        UniqueConstraint(
            "tenant_id",
            "series_code",
            "period_key",
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SeriesRepair(Base, _RepairColumns):
    """Immutable evidence of one tenant counter repair."""

    __tablename__ = "series_repairs"
    __table_args__ = (
        Index("ix_series_repairs_tenant_series", "tenant_id", "series_code"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# ── Platform plane ──────────────────────────────────────────────────────────


class PlatformNumberSeries(Base, _SeriesColumns, TimestampMixin):
    """A control-plane configured series."""

    __tablename__ = "platform_number_series"
    __table_args__ = (
        UniqueConstraint("series_code", name="uq_platform_number_series_code"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()


class PlatformSeriesCounter(Base, _CounterColumns, TimestampMixin):
    """One control-plane counter per (series, period)."""

    __tablename__ = "platform_series_counters"
    __table_args__ = (
        UniqueConstraint(
            "series_code", "period_key", name="uq_platform_series_counters_identity"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()


class PlatformAllocationReceipt(Base, _ReceiptColumns):
    """Immutable evidence of one control-plane allocation."""

    __tablename__ = "platform_allocation_receipts"
    __table_args__ = (
        UniqueConstraint(
            "series_code",
            "period_key",
            "allocated_value",
            name="uq_platform_allocation_receipts_value",
        ),
        Index("ix_platform_allocation_receipts_series", "series_code"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class PlatformSeriesRepair(Base, _RepairColumns):
    """Immutable evidence of one control-plane counter repair."""

    __tablename__ = "platform_series_repairs"
    __table_args__ = (
        Index("ix_platform_series_repairs_series", "series_code"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


TENANT_TABLES: tuple[str, ...] = (
    "number_series",
    "series_counters",
    "allocation_receipts",
    "series_repairs",
)
PLATFORM_TABLES: tuple[str, ...] = (
    "platform_number_series",
    "platform_series_counters",
    "platform_allocation_receipts",
    "platform_series_repairs",
)

#: The append-only tables. Named once so the migration, the service and the
#: canaries cannot disagree about which rows may never change.
IMMUTABLE_TABLES: tuple[str, ...] = (
    "allocation_receipts",
    "series_repairs",
    "platform_allocation_receipts",
    "platform_series_repairs",
)

__all__ = [
    "IMMUTABLE_TABLES",
    "NO_PERIOD",
    "PLATFORM_TABLES",
    "RESET_POLICIES",
    "SCHEMA",
    "TENANT_TABLES",
    "AllocationReceipt",
    "NumberSeries",
    "PlatformAllocationReceipt",
    "PlatformNumberSeries",
    "PlatformSeriesCounter",
    "PlatformSeriesRepair",
    "SeriesCounter",
    "SeriesRepair",
]
