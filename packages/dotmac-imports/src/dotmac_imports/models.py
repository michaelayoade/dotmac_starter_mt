"""The durable import ledger, on the tenant plane only (ADR-0025 § 3, § 6).

Ported from Sub's `import_runs` / `import_run_rows` with three deliberate
differences, each of which is a defect the dossier recorded in the source:

* every row is tenant-scoped — Sub's tables have no tenant column at all;
* no column here is a foreign key into a domain table — Sub's shared row table
  carries `payment_id` and `record_created`, welding one domain's money
  concerns into the ledger every other domain would share;
* the input is a `dotmac-files` id and its checksum rather than the payload
  inline in a `Text` column, which is what Sub's own comment predicted.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("imports")

_JSON_VARIANT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class ImportRun(Base, TimestampMixin):
    """One operator-initiated import of one document, dry run or apply."""

    __tablename__ = "import_runs"
    __table_args__ = (
        # The composite identity every tenant-scoped FK into this table needs.
        UniqueConstraint("tenant_id", "id", name="uq_import_runs_tenant_id_id"),
        # One validated run promotes into at most one apply run. This is what
        # makes "applied exactly once" a database fact rather than a race the
        # orchestrator has to win.
        UniqueConstraint(
            "tenant_id", "source_run_id", name="uq_import_runs_tenant_source_run"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_run_id"],
            [f"{SCHEMA}.import_runs.tenant_id", f"{SCHEMA}.import_runs.id"],
            ondelete="RESTRICT",
            name="fk_import_runs_source_run",
        ),
        Index("ix_import_runs_tenant_id", "tenant_id"),
        Index("ix_import_runs_tenant_status", "tenant_id", "status"),
        Index("ix_import_runs_tenant_kind", "tenant_id", "kind"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), sa.ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    # WHAT is being imported, in the importing domain's own words ("payments",
    # "employees"). An opaque label to this module: it selects nothing here, and
    # the module ships no registry of permitted values.
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # The dry run this apply run was promoted from. NULL for a dry run.
    source_run_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)

    # The stored document, owned by `dotmac-files`. Deliberately NOT a foreign
    # key: an app composes the two modules or it does not, and a hard FK would
    # make the files lineage a hard dependency of this one (ADR-0024).
    source_file_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_layout: Mapped[str] = mapped_column(String(20), nullable=False)
    source_delimiter: Mapped[str] = mapped_column(String(4), nullable=False)
    source_encoding: Mapped[str] = mapped_column(String(40), nullable=False)
    column_mapping: Mapped[list[list[str]] | None] = mapped_column(
        _JSON_VARIANT, nullable=True
    )

    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ok_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(63), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ImportRunRow(Base, TimestampMixin):
    """The outcome of one input line, and nothing about what it meant."""

    __tablename__ = "import_run_rows"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_import_run_rows_tenant_id_id"),
        # Re-processing a run cannot double-record a line.
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "row_number",
            name="uq_import_run_rows_tenant_run_line",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [f"{SCHEMA}.import_runs.tenant_id", f"{SCHEMA}.import_runs.id"],
            ondelete="CASCADE",
            name="fk_import_run_rows_run",
        ),
        Index("ix_import_run_rows_tenant_run", "tenant_id", "run_id"),
        Index("ix_import_run_rows_tenant_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), sa.ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Stable identity for repair/idempotency without copying the imported row
    # into a second retention surface outside dotmac-files.
    row_fingerprint_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(63), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Whatever the domain's applier returned, recorded and never interpreted.
    result: Mapped[dict[str, object] | None] = mapped_column(
        _JSON_VARIANT, nullable=True
    )


TENANT_TABLES: tuple[str, ...] = ("import_runs", "import_run_rows")

__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "ImportRun",
    "ImportRunRow",
]
