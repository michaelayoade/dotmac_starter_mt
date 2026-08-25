"""The first stateful module's declaration must agree with the kernel ledger.

D1's whole claim is that a module cannot quietly re-point its own namespace: the
allocation lives in the kernel, the manifest merely declares it, and construction
of the registry is what checks the two against each other. These tests pin the
agreement AND prove the check bites — a contract with no demonstrated failure
mode is an assertion, not a guarantee.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import dotmac_template_studio as template_studio
import pytest
from dotmac_kernel.modules import ModuleManifest, ModuleRegistry
from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    NamespaceAllocationError,
    NamespaceRegistry,
    UnallocatedNamespaceError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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


def test_dunder_version_matches_the_distribution_and_manifest() -> None:
    """The package is not release-allowlisted yet, so the generic releasable-
    module guard cannot see it. Its three version surfaces must still agree
    before a future adopter can receive an honest wheel and inventory row.
    """
    declared = tomllib.loads(
        (PROJECT_ROOT / "packages/dotmac-template-studio/pyproject.toml").read_text(
            encoding="utf-8"
        )
    )["tool"]["poetry"]["version"]
    assert template_studio.__version__ == declared, (
        f"dotmac_template_studio.__version__ is "
        f"{template_studio.__version__!r} but the distribution declares "
        f"{declared!r}"
    )
    assert (
        MODULE.version == declared
    ), f"manifest version {MODULE.version!r} != distribution {declared!r}"


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

_VERSIONS_DIR = (
    PROJECT_ROOT
    / "packages/dotmac-template-studio/src/dotmac_template_studio/migrations/versions"
)


def _revision_files() -> list[Path]:
    return sorted(p for p in _VERSIONS_DIR.glob("*.py") if p.name != "__init__.py")


def test_the_lineage_ships_at_least_one_revision() -> None:
    """Assert on the set walked: an empty lineage would make every check below
    pass over nothing, and the module's tables would never be created."""
    assert _revision_files(), f"no revisions found under {_VERSIONS_DIR}"


def test_every_revision_id_uses_the_allocated_prefix_and_fits_the_column() -> None:
    pattern = re.compile(rf"^{LEDGER_ROW.prefix}_\d{{4}}_[a-z0-9_]+$")
    for path in _revision_files():
        text = path.read_text(encoding="utf-8")
        match = re.search(r'^revision = "([^"]+)"', text, re.MULTILINE)
        assert match, f"{path.name}: no `revision` assignment"
        revision = match.group(1)
        assert pattern.match(revision), (
            f"{path.name}: revision {revision!r} does not match the allocated "
            f"prefix pattern {pattern.pattern}"
        )
        # `alembic_version.version_num` is VARCHAR(32); a longer id fails only
        # against a real database, mid-deploy.
        assert len(revision) <= 32, f"{revision!r} exceeds 32 characters"


def test_the_lineage_root_carries_the_owner_branch_label() -> None:
    """How an `alembic_version` row is attributed to this module."""
    roots = [
        path
        for path in _revision_files()
        if re.search(r"^down_revision = None$", path.read_text(encoding="utf-8"), re.M)
    ]
    assert len(roots) == 1, f"expected exactly one lineage root, found {len(roots)}"
    text = roots[0].read_text(encoding="utf-8")
    assert f'branch_labels = ("{LEDGER_ROW.branch_label}",)' in text


def test_no_revision_crosses_lineages_with_down_revision() -> None:
    """Cross-lineage ordering is `depends_on`; `down_revision` would splice two
    independently released lineages into one chain."""
    for path in _revision_files():
        text = path.read_text(encoding="utf-8")
        match = re.search(r'^down_revision = "([^"]+)"', text, re.MULTILINE)
        if match:
            assert match.group(1).startswith(f"{LEDGER_ROW.prefix}_"), (
                f"{path.name}: down_revision {match.group(1)!r} points outside "
                "this module's lineage — use `depends_on` instead"
            )


def test_declared_tables_match_what_the_lineage_creates() -> None:
    created = set()
    for path in _revision_files():
        text = path.read_text(encoding="utf-8")
        created |= set(re.findall(r'^_(?:TEMPLATES|VERSIONS) = "([^"]+)"', text, re.M))
    assert created == set(MODULE.tables), (
        f"manifest declares {sorted(MODULE.tables)} but the lineage creates "
        f"{sorted(created)} — the composed gate rejects a create outside the "
        "declaration, and the live-catalog gate checks it in both directions"
    )
