"""Architecture canaries for Collections' one behavior on two planes."""

from __future__ import annotations

from collections.abc import Iterable

from dotmac_collections import models
from dotmac_collections.manifest import module
from dotmac_kernel.planes import ModulePlane

EXPECTED_PLATFORM_TABLES = tuple(f"platform_{table}" for table in models.TENANT_TABLES)
EXPECTED_PLANE_SETS = {
    (ModulePlane.TENANT,),
    (ModulePlane.PLATFORM,),
    (ModulePlane.TENANT, ModulePlane.PLATFORM),
}


def _dual_plane_problems(
    tenant_tables: Iterable[str],
    platform_tables: Iterable[str],
    supported_plane_sets: Iterable[tuple[ModulePlane, ...]],
) -> tuple[str, ...]:
    tenant = tuple(tenant_tables)
    platform = tuple(platform_tables)
    problems: list[str] = []
    if platform != tuple(f"platform_{table}" for table in tenant):
        problems.append("platform-table-mirror-drift")
    if set(supported_plane_sets) != EXPECTED_PLANE_SETS:
        problems.append("selectable-plane-contract-drift")
    return tuple(problems)


def test_dual_plane_detector_proves_its_sensitivity() -> None:
    assert _dual_plane_problems(
        ("collection_cases",),
        (),
        ((ModulePlane.TENANT,),),
    ) == (
        "platform-table-mirror-drift",
        "selectable-plane-contract-drift",
    )


def test_manifest_declares_a_complete_selectable_platform_mirror() -> None:
    assert not _dual_plane_problems(
        module.tables,
        module.platform_tables,
        module.supported_plane_sets,
    )
    assert module.tables == models.TENANT_TABLES
    assert module.platform_tables == models.PLATFORM_TABLES
    assert models.PLATFORM_TABLES == EXPECTED_PLATFORM_TABLES


def test_tenant_and_platform_models_have_opposite_scope_shapes() -> None:
    mapped = {
        table.name: table
        for table in models.Base.metadata.tables.values()
        if table.schema == models.SCHEMA
    }
    assert set(mapped) == set(models.TENANT_TABLES) | set(models.PLATFORM_TABLES)

    for table_name in models.TENANT_TABLES:
        assert mapped[table_name].c.tenant_id.nullable is False
    for table_name in models.PLATFORM_TABLES:
        assert "tenant_id" not in mapped[table_name].c


def test_no_collections_foreign_key_crosses_a_plane() -> None:
    tenant = set(models.TENANT_TABLES)
    platform = set(models.PLATFORM_TABLES)
    for table in models.Base.metadata.tables.values():
        if table.schema != models.SCHEMA:
            continue
        other_plane = platform if table.name in tenant else tenant
        for foreign_key in table.foreign_keys:
            assert foreign_key.column.table.name not in other_plane
