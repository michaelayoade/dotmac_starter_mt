"""The first stateful module's declaration must agree with the kernel ledger.

D1's whole claim is that a module cannot quietly re-point its own namespace: the
allocation lives in the kernel, the manifest merely declares it, and construction
of the registry is what checks the two against each other. These tests pin the
agreement AND prove the check bites — a contract with no demonstrated failure
mode is an assertion, not a guarantee.
"""

from __future__ import annotations

import dotmac_template_studio as template_studio
import pytest
from dotmac_kernel.modules import ModuleManifest, ModuleRegistry
from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    NamespaceAllocationError,
    NamespaceRegistry,
    UnallocatedNamespaceError,
)

MODULE = template_studio.module
LEDGER_ROW = next(
    owner for owner in MIGRATION_OWNER_LEDGER if owner.owner == "template_studio"
)


def test_the_module_is_stateful() -> None:
    assert MODULE.is_stateful
    assert MODULE.db_schema == "mod_tstudio"


def test_the_manifest_matches_its_ledger_allocation() -> None:
    assert MODULE.migration_owner() == LEDGER_ROW


def test_the_registry_accepts_the_declared_module() -> None:
    registry = NamespaceRegistry.from_manifests([MODULE])
    assert LEDGER_ROW in registry.owners()


def test_a_module_cannot_invent_an_unallocated_namespace() -> None:
    """The sensitivity proof for `UnallocatedNamespaceError`."""
    impostor = ModuleManifest(
        code="not_allocated",
        version="0.0.1",
        short_code="ghost",
        migration_prefix="gh",
        tables=("things",),
    )
    with pytest.raises(UnallocatedNamespaceError):
        NamespaceRegistry.from_manifests([impostor])


def test_a_module_cannot_re_point_its_allocated_schema() -> None:
    """The sensitivity proof for `NamespaceAllocationError` — the data-loss case.

    A module keeping its `code` but changing its `short_code` would move where
    its data physically lives; the ledger is what makes that unrepresentable.
    """
    moved = ModuleManifest(
        code="template_studio",
        version=MODULE.version,
        short_code="tstudio2",
        migration_prefix=MODULE.migration_prefix,
        migration_branch=MODULE.migration_branch,
        tables=MODULE.tables,
    )
    with pytest.raises((NamespaceAllocationError, UnallocatedNamespaceError)):
        NamespaceRegistry.from_manifests([moved])


def test_the_module_composes_with_the_assembly_features() -> None:
    """The real composition boots — unique codes, no cycle, no namespace clash."""
    from app.assembly import assembly

    registry = ModuleRegistry(assembly.modules)
    assert "template_studio" in {m.code for m in registry.enabled_order()}


# ── The migration lineage ───────────────────────────────────────────────────
#
# Deliberately EMPTY. This file used to hand-roll five lineage checks here —
# revision-id prefix and length, one lineage root carrying the owner's branch
# label, `down_revision` never crossing lineages, and declared tables matching
# what the lineage creates — each with its own regex over the revision files.
#
# All five are rules `dotmac_kernel.migrations.gate` already owns and enforces
# GENERICALLY, off the manifest, over the same files, in `make check` (via
# `scripts/migration_gate.py`) and in `tests/unit/test_migration_gate.py`. The
# copies here were a second authority for one set of rules, scoped to one
# module and weaker than the originals: the table check keyed off a regex that
# only matched Template Studio's own `_TEMPLATES`/`_VERSIONS` constant names,
# so it could not have been reused by a second module even in principle.
#
# The last of the five to become generic was declared-but-never-created, which
# the static gate did not cover until `_check_declared_tables_are_built` landed
# alongside this deletion; it was reachable only through the live catalog
# validator, against an already-migrated PostgreSQL.
#
# A new module inherits all five by being registered in the ledger and having
# its version location selected in `alembic.ini` — not by copying this block.
# The rule for anything added later: a new module-conformance check belongs
# inside `run_gate`, so every existing caller picks it up with no call-site
# change (ADR-0006 amendment 2026-08-11).
