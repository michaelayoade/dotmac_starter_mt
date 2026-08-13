"""Entitlement Allocation must coexist with its extraction source in shadow.

The vendor control plane cannot cut over atomically without first installing
both schemas. Its legacy projection uses the same domain class names, so any ORM
relationship resolved through SQLAlchemy's global string registry is ambiguous.
This subprocess keeps the deliberate duplicate registrations out of the main
test process while exercising real mapper configuration.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_same_named_legacy_models_can_share_the_kernel_base() -> None:
    probe = textwrap.dedent(
        """
        from __future__ import annotations

        from uuid import UUID

        from dotmac_entitlement_allocation.models import (
            Allocation as ModuleAllocation,
            AllocationEntry as ModuleAllocationEntry,
        )
        from dotmac_kernel.models import Base, uuid_pk
        from sqlalchemy import ForeignKey, String
        from sqlalchemy.orm import (
            Mapped,
            configure_mappers,
            mapped_column,
            relationship,
        )

        # Keep aliases live so the module registrations cannot be optimized away.
        assert ModuleAllocation.__name__ == "Allocation"
        assert ModuleAllocationEntry.__name__ == "AllocationEntry"

        class Allocation(Base):
            __tablename__ = "allocations"
            __table_args__ = {"schema": "legacy_shadow_probe"}

            id: Mapped[UUID] = uuid_pk()
            entries: Mapped[list[AllocationEntry]] = relationship(
                lambda: AllocationEntry,
                back_populates="allocation",
                order_by=lambda: AllocationEntry.capability_code,
            )

        class AllocationEntry(Base):
            __tablename__ = "allocation_entries"
            __table_args__ = {"schema": "legacy_shadow_probe"}

            id: Mapped[UUID] = uuid_pk()
            allocation_id: Mapped[UUID] = mapped_column(
                ForeignKey("legacy_shadow_probe.allocations.id"),
                nullable=False,
            )
            capability_code: Mapped[str] = mapped_column(String(120), nullable=False)
            allocation: Mapped[Allocation] = relationship(
                lambda: Allocation,
                back_populates="entries",
            )

        configure_mappers()
        """
    )
    result = subprocess.run(  # noqa: S603 # fixed interpreter, no shell/input
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
