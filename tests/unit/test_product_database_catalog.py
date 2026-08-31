"""Build-once database catalogues are typed declarations, never live transcripts."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from dotmac_kernel import (
    KERNEL_MODULE_CONTRACT_VERSION,
    ComposedDatabaseLineageHeadV1,
    DatabaseCatalogDeclarationScope,
    DatabaseCatalogFactAttribute,
    DatabaseCatalogFactDirection,
    DatabaseCatalogOwnerKind,
    DatabaseCatalogOwnerV1,
    DatabaseColumnContractV1,
    DatabaseColumnGeneration,
    DatabasePersistencePlane,
    DatabaseRelationKind,
    DatabaseTableContractV1,
    HostDatabaseCatalogFragmentV1,
    ModuleDatabaseCatalogContributionV1,
    ModuleDatabaseCatalogSnapshot,
    ModuleDatabaseTableContractV1,
    ModuleManifest,
    ObservedDatabaseTableV1,
    PostgresTablesColumnsObservationV1,
    PostgresTypeContractV1,
    PostgresTypeKind,
    ProductAssemblySpec,
    ProductDatabaseCatalogDigestMismatchError,
    ProductDatabaseCatalogError,
    ProductDatabaseCatalogSnapshot,
    compare_module_database_catalog,
    verify_module_database_catalog,
)

_MODULE_TABLES = (
    "deployment_plans",
    "deployment_targets",
    "observation_attempts",
    "observation_receipts",
    "rollout_attempts",
    "rollouts",
    "target_credentials",
)


def _type(formatted: str = "uuid") -> PostgresTypeContractV1:
    return PostgresTypeContractV1(
        kind=PostgresTypeKind.BASE,
        schema="pg_catalog",
        name="uuid" if formatted == "uuid" else "varchar",
        formatted=formatted,
    )


def _columns(
    count: int, *, digest_width: int = 64
) -> tuple[DatabaseColumnContractV1, ...]:
    return tuple(
        DatabaseColumnContractV1(
            name=f"column_{ordinal:03d}",
            ordinal=ordinal,
            postgres_type=(
                _type(f"character varying({digest_width})") if ordinal == 1 else _type()
            ),
            nullable=ordinal != 1,
        )
        for ordinal in range(1, count + 1)
    )


def _module(*, digest_width: int = 64, include_catalog: bool = True) -> ModuleManifest:
    # 14 + 14 + 14 + 14 + 13 + 13 + 13 = 95 exact column declarations.
    counts = (14, 14, 14, 14, 13, 13, 13)
    contribution = ModuleDatabaseCatalogContributionV1(
        lineage_head="dc_0002_canonical_plan_digest",
        tables=tuple(
            ModuleDatabaseTableContractV1(
                name=name,
                relation_kind=DatabaseRelationKind.TABLE,
                columns=_columns(count, digest_width=digest_width),
            )
            for name, count in zip(_MODULE_TABLES, counts, strict=True)
        ),
    )
    return ModuleManifest(
        code="deployment_control",
        version="0.1.0a3",
        core=False,
        short_code="deploy",
        migration_prefix="dc",
        migration_branch="deployment_control",
        platform_tables=_MODULE_TABLES,
        database_catalog=contribution if include_catalog else None,
    )


def _kernel_fragment() -> HostDatabaseCatalogFragmentV1:
    owner = DatabaseCatalogOwnerV1(DatabaseCatalogOwnerKind.KERNEL, "kernel")
    return HostDatabaseCatalogFragmentV1(
        owner=owner,
        lineage_head="0034_example_kernel_head",
        tables=(
            DatabaseTableContractV1(
                schema="public",
                name="kernel_contract_marker",
                owner=owner,
                plane=DatabasePersistencePlane.HOST,
                relation_kind=DatabaseRelationKind.TABLE,
                columns=_columns(1),
            ),
        ),
    )


def _assembly_fragment() -> HostDatabaseCatalogFragmentV1:
    owner = DatabaseCatalogOwnerV1(DatabaseCatalogOwnerKind.ASSEMBLY, "platform-cp")
    return HostDatabaseCatalogFragmentV1(
        owner=owner,
        lineage_head="a999_catalog_fixture",
        tables=(
            DatabaseTableContractV1(
                schema="public",
                name="assembly_contract_marker",
                owner=owner,
                plane=DatabasePersistencePlane.HOST,
                relation_kind=DatabaseRelationKind.TABLE,
                columns=_columns(1),
            ),
        ),
    )


def _snapshot(*, digest_width: int = 64) -> ProductDatabaseCatalogSnapshot:
    return ProductDatabaseCatalogSnapshot.from_assembly(
        ProductAssemblySpec(
            name="platform-cp", modules=(_module(digest_width=digest_width),)
        ),
        product_version="8.0.0",
        postgres_major=16,
        host_fragments=(_kernel_fragment(), _assembly_fragment()),
        composed_lineage_heads=(
            ComposedDatabaseLineageHeadV1(
                DatabaseCatalogOwnerV1(
                    DatabaseCatalogOwnerKind.ASSEMBLY, "platform-cp"
                ),
                "a999_catalog_fixture",
            ),
            ComposedDatabaseLineageHeadV1(
                DatabaseCatalogOwnerV1(DatabaseCatalogOwnerKind.KERNEL, "kernel"),
                "0034_example_kernel_head",
            ),
            ComposedDatabaseLineageHeadV1(
                DatabaseCatalogOwnerV1(
                    DatabaseCatalogOwnerKind.MODULE, "deployment_control"
                ),
                "dc_0002_canonical_plan_digest",
            ),
        ),
    )


def _module_snapshot() -> ModuleDatabaseCatalogSnapshot:
    return ModuleDatabaseCatalogSnapshot.from_manifest(
        _module(),
        distribution_name="dotmac-deployment-control",
        distribution_version="0.1.0a7",
        composed_lineage_head=ComposedDatabaseLineageHeadV1(
            DatabaseCatalogOwnerV1(
                DatabaseCatalogOwnerKind.MODULE, "deployment_control"
            ),
            "dc_0002_canonical_plan_digest",
        ),
    )


def test_module_snapshot_is_publishable_without_product_completeness() -> None:
    snapshot = _module_snapshot()

    assert snapshot.distribution_version == "0.1.0a7"
    assert snapshot.module_release_version == "0.1.0a3"
    # The snapshot RECORDS the generation the manifest was built against; it
    # does not restate a constant of its own. `_module()` declares no legacy
    # web surface, so the kernel infers the current generation for it — and a
    # manifest that pins the older one must come through as that older one,
    # which is what stops this reading as "equal to whatever the kernel says".
    assert snapshot.manifest_contract_version == KERNEL_MODULE_CONTRACT_VERSION
    assert (
        ModuleDatabaseCatalogSnapshot.from_manifest(
            replace(_module(), contract_version=1),
            distribution_name="dotmac-deployment-control",
            distribution_version="0.1.0a7",
            composed_lineage_head=ComposedDatabaseLineageHeadV1(
                DatabaseCatalogOwnerV1(
                    DatabaseCatalogOwnerKind.MODULE, "deployment_control"
                ),
                "dc_0002_canonical_plan_digest",
            ),
        ).manifest_contract_version
        == 1
    )
    assert len(snapshot.tables) == 7
    assert sum(len(table.columns) for table in snapshot.tables) == 95
    assert (
        ModuleDatabaseCatalogSnapshot.from_json_bytes(
            snapshot.to_json_bytes(), expected_digest=snapshot.digest
        )
        == snapshot
    )


def test_changed_type_emits_declared_absent_and_observed_present() -> None:
    snapshot = _module_snapshot()
    tables = tuple(
        ObservedDatabaseTableV1(
            schema=table.schema,
            name=table.name,
            relation_kind=table.relation_kind,
            columns=(
                (
                    replace(
                        table.columns[0],
                        postgres_type=_type("character varying(128)"),
                    ),
                    *table.columns[1:],
                )
                if table.name == "deployment_plans"
                else table.columns
            ),
        )
        for table in snapshot.tables
    )
    observation = PostgresTablesColumnsObservationV1(
        postgres_major=16,
        covered_schemas=("mod_deploy",),
        present_schemas=("mod_deploy",),
        tables=tables,
        database_extent_complete=False,
    )

    comparison = compare_module_database_catalog(snapshot, observation)
    facts = [
        (drift.attribute, drift.direction)
        for drift in comparison.drifts
        if drift.table == "deployment_plans"
    ]
    assert facts == [
        (
            DatabaseCatalogFactAttribute.POSTGRES_TYPE,
            DatabaseCatalogFactDirection.DECLARED_BUT_ABSENT,
        ),
        (
            DatabaseCatalogFactAttribute.POSTGRES_TYPE,
            DatabaseCatalogFactDirection.PRESENT_BUT_UNDECLARED,
        ),
    ]
    verified = verify_module_database_catalog(
        declaration_bytes=snapshot.to_json_bytes(),
        declaration_digest=snapshot.digest,
        observation_bytes=observation.to_json_bytes(),
        observation_digest=observation.digest,
    )
    assert verified == comparison
    assert verified.postgres_major == 16
    assert verified.declaration_schema == "dotmac.module-database-catalog/v1"
    assert verified.declaration_scope is DatabaseCatalogDeclarationScope.MODULE
    assert verified.complete_schemas == ("mod_deploy",)
    assert verified.product_code == ""
    assert verified.product_version == ""


def test_composition_proves_the_seven_table_ninety_five_column_shape() -> None:
    snapshot = _snapshot()
    fragment = next(
        fragment
        for fragment in snapshot.fragments
        if fragment.owner.kind is DatabaseCatalogOwnerKind.MODULE
    )

    assert fragment.owner.code == "deployment_control"
    assert fragment.selected_planes == (DatabasePersistencePlane.PLATFORM,)
    assert len(fragment.tables) == 7
    assert sum(len(table.columns) for table in fragment.tables) == 95
    assert {table.schema for table in fragment.tables} == {"mod_deploy"}
    assert {table.plane for table in fragment.tables} == {
        DatabasePersistencePlane.PLATFORM
    }


def test_type_modifiers_are_digest_bound_even_when_counts_do_not_change() -> None:
    before = _snapshot(digest_width=64)
    after = _snapshot(digest_width=128)

    assert len(before.fragments) == len(after.fragments)
    assert sum(
        len(table.columns) for item in before.fragments for table in item.tables
    ) == sum(len(table.columns) for item in after.fragments for table in item.tables)
    assert before.digest != after.digest


def test_snapshot_round_trips_only_its_canonical_bytes() -> None:
    snapshot = _snapshot()

    assert (
        ProductDatabaseCatalogSnapshot.from_json_bytes(
            snapshot.to_json_bytes(), expected_digest=snapshot.digest
        )
        == snapshot
    )

    pretty = json.dumps(
        json.loads(snapshot.to_json_bytes()), indent=2, sort_keys=True
    ).encode()
    with pytest.raises(ProductDatabaseCatalogError, match="canonical"):
        ProductDatabaseCatalogSnapshot.from_json_bytes(pretty)
    with pytest.raises(
        ProductDatabaseCatalogDigestMismatchError, match="does not match"
    ):
        ProductDatabaseCatalogSnapshot.from_json_bytes(
            snapshot.to_json_bytes(), expected_digest="sha256:" + "0" * 64
        )


def test_snapshot_cannot_be_hand_assembled_around_composition() -> None:
    composed = _snapshot()

    with pytest.raises(ProductDatabaseCatalogError, match="factory-only"):
        ProductDatabaseCatalogSnapshot(
            product_code=composed.product_code,
            product_version=composed.product_version,
            postgres_major=composed.postgres_major,
            complete_schemas=composed.complete_schemas,
            fragments=composed.fragments,
        )


def test_duplicate_json_fields_are_refused_not_normalized() -> None:
    payload = (
        _snapshot()
        .to_json_bytes()
        .replace(b'{"complete_schemas"', b'{"schema":"duplicate","complete_schemas"', 1)
    )

    with pytest.raises(ProductDatabaseCatalogError, match="duplicate JSON field"):
        ProductDatabaseCatalogSnapshot.from_json_bytes(payload)


def test_structured_identifiers_preserve_quoted_postgres_spelling() -> None:
    owner = DatabaseCatalogOwnerV1(DatabaseCatalogOwnerKind.ASSEMBLY, "quoted-fixture")
    table = DatabaseTableContractV1(
        schema="Tenant.Schema",
        name="events:source->target",
        owner=owner,
        plane=DatabasePersistencePlane.PLATFORM,
        relation_kind=DatabaseRelationKind.TABLE,
        columns=(
            DatabaseColumnContractV1(
                name="payload.value",
                ordinal=1,
                postgres_type=_type(),
                nullable=False,
            ),
        ),
    )

    assert table.coordinate == ("Tenant.Schema", "events:source->target")
    assert table.columns[0].name == "payload.value"


def test_module_detail_cannot_invent_or_omit_a_manifest_table() -> None:
    contribution = ModuleDatabaseCatalogContributionV1(
        lineage_head="dc_0002_canonical_plan_digest",
        tables=(
            ModuleDatabaseTableContractV1(
                name="unknown_table",
                relation_kind=DatabaseRelationKind.TABLE,
                columns=_columns(1),
            ),
        ),
    )

    with pytest.raises(ValueError, match="missing=.*deployment_plans.*unknown"):
        replace(_module(), database_catalog=contribution)


def test_complete_snapshot_refuses_a_selected_module_without_structure() -> None:
    assembly = ProductAssemblySpec(
        name="platform-cp", modules=(_module(include_catalog=False),)
    )

    # Both host fragments, so the composition reaches the module-completeness
    # check this test is named for. With only the kernel fragment it refused
    # for the missing ASSEMBLY fragment instead — a pass that proved
    # `test_assembly_host_fragment_is_required_for_complete_public_extent`
    # twice and proved this test's property not at all.
    with pytest.raises(ProductDatabaseCatalogError, match="no database_catalog"):
        ProductDatabaseCatalogSnapshot.from_assembly(
            assembly,
            product_version="8.0.0",
            postgres_major=16,
            host_fragments=(_kernel_fragment(), _assembly_fragment()),
            composed_lineage_heads=(),
        )


def test_assembly_host_fragment_is_required_for_complete_public_extent() -> None:
    with pytest.raises(ProductDatabaseCatalogError, match="assembly-owned fragment"):
        ProductDatabaseCatalogSnapshot.from_assembly(
            ProductAssemblySpec(name="platform-cp", modules=(_module(),)),
            product_version="8.0.0",
            postgres_major=16,
            host_fragments=(_kernel_fragment(),),
            composed_lineage_heads=(),
        )


def test_module_lineage_head_must_belong_to_its_migration_owner() -> None:
    with pytest.raises(ValueError, match="does not belong"):
        replace(
            _module(),
            database_catalog=replace(
                _module().database_catalog,
                lineage_head="xx_0001_wrong_owner",
            ),
        )


def test_host_fragment_cannot_impersonate_a_module() -> None:
    module_owner = DatabaseCatalogOwnerV1(
        DatabaseCatalogOwnerKind.MODULE, "deployment_control"
    )

    with pytest.raises(ProductDatabaseCatalogError, match="cannot use the module"):
        replace(_kernel_fragment(), owner=module_owner)


@pytest.mark.parametrize(
    ("generation", "expression"),
    [
        (DatabaseColumnGeneration.DEFAULT, ""),
        (DatabaseColumnGeneration.GENERATED_STORED, ""),
        (DatabaseColumnGeneration.NONE, "now()"),
    ],
)
def test_generation_and_expression_cannot_contradict(
    generation: DatabaseColumnGeneration, expression: str
) -> None:
    with pytest.raises(ProductDatabaseCatalogError):
        DatabaseColumnContractV1(
            name="created_at",
            ordinal=1,
            postgres_type=_type(),
            nullable=False,
            generation=generation,
            expression=expression,
        )


def test_kernel_host_fragment_is_required_for_complete_public_extent() -> None:
    with pytest.raises(ProductDatabaseCatalogError, match="kernel-owned fragment"):
        ProductDatabaseCatalogSnapshot.from_assembly(
            ProductAssemblySpec(name="platform-cp", modules=(_module(),)),
            product_version="8.0.0",
            postgres_major=16,
            host_fragments=(),
            composed_lineage_heads=(),
        )
