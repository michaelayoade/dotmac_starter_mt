"""Database declarations are compared with live facts in both directions.

The Platform CP bootstrap incident is the sensitivity case: the accepted
descriptor omitted a schema, migration heads and a revocation that were all
present in production.  A declared-only validator cannot see that shape; exact
set comparison must report the live facts that the descriptor does not name.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import pytest
from dotmac_deployment_foundation.database_drift import (
    POSTGRES_SYSTEM_EXCLUSIONS,
    DatabaseContractDriftReport,
    DatabaseContractGapCode,
    DatabaseDriftClassification,
    DatabaseDriftExclusions,
    DatabaseDriftPhase,
    DatabaseFactDimension,
    DatabaseFactDirection,
    EffectivePrivilegeAuditUniverse,
    EffectivePrivilegeExclusion,
    EffectivePrivilegeKey,
    NameExclusion,
    ObservedDatabaseState,
    PrefixExclusion,
    PrivilegeUniverseDerivation,
    compare_database_contract,
)
from dotmac_deployment_foundation.database_structure import (
    DatabaseCatalogCoordinateV1,
    DatabaseCatalogProductIdentityV1,
    DatabaseCatalogScope,
    DatabaseDescriptorCatalogBindingV1,
    DatabaseStructureComparisonResultV1,
    DatabaseStructureCoverageV1,
    DatabaseStructureFactAttribute,
    DatabaseStructureFactKeyV1,
    DatabaseStructureFindingV1,
    DatabaseStructureObservationEvidenceV1,
    DatabaseStructureWitnessV1,
    StructureFactDimension,
    StructureFactDirection,
    accept_database_structure_comparison,
)
from dotmac_deployment_foundation.document import build_canonical_document
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.recovery import (
    CatalogEvidence,
    EffectivePrivilegeFact,
    MembershipFact,
    RoleFact,
    TablespaceDecision,
)
from dotmac_deployment_foundation.render.compose import configuration_digest
from dotmac_deployment_foundation.spec import (
    SCHEMA_V1,
    SCHEMA_V2,
    DatabaseContract,
    DatabaseRole,
    IsolationInvariant,
    ProductDeploymentSpec,
)

MODULE_CATALOG_PAYLOAD = b'{"schema":"dotmac.module-database-catalog/v1"}'
PRODUCT_CATALOG_PAYLOAD = b'{"schema":"dotmac.product-database-catalog/v1"}'
OBSERVATION_PAYLOAD = b'{"schema":"dotmac.postgresql-tables-columns-observation/v1"}'


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


MODULE_CATALOG = DatabaseCatalogCoordinateV1(
    schema="dotmac.module-database-catalog/v1",
    path="catalogs/deployment-control-database.json",
    digest=_digest(MODULE_CATALOG_PAYLOAD),
    scope=DatabaseCatalogScope.MODULE,
    complete_schemas=("mod_deploy",),
)
PRODUCT_CATALOG = DatabaseCatalogCoordinateV1(
    schema="dotmac.product-database-catalog/v1",
    path="catalogs/product-database.json",
    digest=_digest(PRODUCT_CATALOG_PAYLOAD),
    scope=DatabaseCatalogScope.PRODUCT,
    complete_schemas=("mod_deploy", "public"),
    product_identity=DatabaseCatalogProductIdentityV1(
        descriptor_product="dotmac_starter_mt",
        catalog_product="dotmac-starter-mt",
        catalog_version="0.1.0",
        mapping_ref="docs/adr/0070-deployment-is-a-stateless-versioned-foundation.md",
    ),
)
STRUCTURE_OBSERVATION = DatabaseStructureObservationEvidenceV1(
    schema="dotmac.postgresql-tables-columns-observation/v1",
    digest=_digest(OBSERVATION_PAYLOAD),
    ref="recovery/catalog/2026-08-31T05:44:36Z",
    postgres_major=16,
    payload=OBSERVATION_PAYLOAD,
)


@dataclasses.dataclass(frozen=True)
class _VerifiedDrift:
    dimension: str
    direction: str
    schema: str
    table: str
    column: str
    attribute: str
    declared: str
    observed: str


@dataclasses.dataclass(frozen=True)
class _VerifiedComparison:
    drifts: tuple[_VerifiedDrift, ...]
    declaration_digest: str
    observation_digest: str
    postgres_major: int
    declaration_schema: str
    declaration_scope: str
    complete_schemas: tuple[str, ...]
    product_code: str
    product_version: str
    measurement_issues: tuple[str, ...] = ()
    comparator_id: str = "dotmac.database-catalog-tables-columns-comparator/v1"
    scope: str = "tables_and_columns"


class _RecordingVerifier:
    def __init__(self, result: _VerifiedComparison) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        *,
        declaration_bytes: bytes,
        declaration_digest: str,
        observation_bytes: bytes,
        observation_digest: str,
    ) -> _VerifiedComparison:
        self.calls.append(
            {
                "declaration_bytes": declaration_bytes,
                "declaration_digest": declaration_digest,
                "observation_bytes": observation_bytes,
                "observation_digest": observation_digest,
            }
        )
        return self.result


def _contract() -> DatabaseContract:
    return DatabaseContract(
        postgres_major=16,
        roles=(
            DatabaseRole(
                name="app_admin",
                kind="migration_owner",
                inherit=True,
                login=True,
                bypassrls=True,
                member_of=(),
            ),
            DatabaseRole(
                name="platform_api",
                kind="platform_app",
                inherit=False,
                login=True,
                bypassrls=False,
                member_of=("app_admin",),
            ),
        ),
        expected_schemas=("public", "mod_deploy"),
        isolation=(
            IsolationInvariant(
                code="platform-api-delete-is-revoked",
                role="platform_api",
                scope="table",
                objects=("public.licence_delivery_targets",),
                privileges=("DELETE",),
                denied=True,
            ),
        ),
        tablespaces="none",
    )


def _catalog() -> CatalogEvidence:
    return CatalogEvidence(
        roles=(
            RoleFact(
                name="app_admin",
                can_login=True,
                inherit=True,
                superuser=False,
                createrole=False,
                createdb=False,
                replication=False,
                bypassrls=True,
            ),
            RoleFact(
                name="platform_api",
                can_login=True,
                inherit=False,
                superuser=False,
                createrole=False,
                createdb=False,
                replication=False,
                bypassrls=False,
            ),
            # Explicit PostgreSQL-owned facts are ignored only because the
            # caller selected the typed system exclusions below.
            RoleFact(
                name="pg_monitor",
                can_login=False,
                inherit=True,
                superuser=False,
                createrole=False,
                createdb=False,
                replication=False,
                bypassrls=False,
            ),
        ),
        memberships=(MembershipFact(member="platform_api", role="app_admin"),),
        effective_privileges=(
            EffectivePrivilegeFact(
                role="platform_api",
                scope="table",
                identity="public.licence_delivery_targets",
                privilege="DELETE",
                holds=False,
            ),
        ),
        schemas=(
            "public",
            "mod_deploy",
            "pg_catalog",
            "information_schema",
            "pg_toast",
            "pg_temp_3",
        ),
        migration_heads=("kernel_0016", "deploy_0001"),
        tablespaces=TablespaceDecision(kind="none"),
    )


BASE_PRODUCT_PATH = Path(__file__).resolve().parents[2] / "deploy" / "product.toml"
BASE_SPEC = ProductDeploymentSpec.load(BASE_PRODUCT_PATH)


def _spec(
    *,
    contract: DatabaseContract | None = None,
    expected_heads: tuple[str, ...] = ("kernel_0016", "deploy_0001"),
) -> ProductDeploymentSpec:
    return dataclasses.replace(
        BASE_SPEC,
        database=contract or _contract(),
        migration=dataclasses.replace(
            BASE_SPEC.migration, expected_heads=expected_heads
        ),
    )


def _observe(
    catalog: CatalogEvidence | None = None,
    *,
    structure_observation: DatabaseStructureObservationEvidenceV1 | None = (
        STRUCTURE_OBSERVATION
    ),
) -> ObservedDatabaseState:
    snapshot = catalog or _catalog()
    audit_facts = snapshot.effective_privileges or _catalog().effective_privileges
    return ObservedDatabaseState(
        postgres_major=16,
        catalog=snapshot,
        effective_privilege_universe=EffectivePrivilegeAuditUniverse(
            keys=frozenset(
                EffectivePrivilegeKey.from_fact(fact) for fact in audit_facts
            ),
            derivation=PrivilegeUniverseDerivation.CATALOG_DISCOVERY,
        ),
        structure_observation=structure_observation,
    )


def _compare(
    *,
    contract: DatabaseContract | None = None,
    catalog: CatalogEvidence | None = None,
    expected_heads: tuple[str, ...] = ("kernel_0016", "deploy_0001"),
    phase: DatabaseDriftPhase = DatabaseDriftPhase.PREFLIGHT,
    exclusions: DatabaseDriftExclusions = POSTGRES_SYSTEM_EXCLUSIONS,
    catalog_binding: DatabaseDescriptorCatalogBindingV1 | None = None,
    structure_witnesses: tuple[DatabaseStructureWitnessV1, ...] = (),
) -> DatabaseContractDriftReport:
    return compare_database_contract(
        _spec(contract=contract, expected_heads=expected_heads),
        _observe(catalog),
        phase=phase,
        exclusions=exclusions,
        catalog_binding=catalog_binding,
        structure_witnesses=structure_witnesses,
    )


def _binding(
    spec: ProductDeploymentSpec,
    catalog: DatabaseCatalogCoordinateV1 = MODULE_CATALOG,
) -> DatabaseDescriptorCatalogBindingV1:
    return DatabaseDescriptorCatalogBindingV1.from_spec(spec, catalogs=(catalog,))


def _structure_result(
    binding: DatabaseDescriptorCatalogBindingV1,
    *,
    coverage: DatabaseStructureCoverageV1 | None = None,
    findings: tuple[DatabaseStructureFindingV1, ...] = (),
    measurement_issues: tuple[str, ...] = (),
) -> DatabaseStructureComparisonResultV1:
    return DatabaseStructureComparisonResultV1(
        descriptor_digest=binding.descriptor_digest,
        binding_digest=binding.sha256_digest(),
        catalog=binding.catalogs[0],
        comparator_contract="dotmac.database-catalog-tables-columns-comparator/v1",
        postgres_major=16,
        observation=STRUCTURE_OBSERVATION,
        observed_at="2026-08-31T05:44:36Z",
        coverage=coverage
        or DatabaseStructureCoverageV1(
            schemas_complete=True,
            tables_complete=True,
            columns_complete=True,
        ),
        findings=findings,
        measurement_issues=measurement_issues,
    )


def _verified_comparison(
    catalog: DatabaseCatalogCoordinateV1,
    *,
    drifts: tuple[_VerifiedDrift, ...] = (),
    comparator_id: str = "dotmac.database-catalog-tables-columns-comparator/v1",
    declaration_digest: str | None = None,
) -> _VerifiedComparison:
    return _VerifiedComparison(
        drifts=drifts,
        declaration_digest=declaration_digest or catalog.digest,
        observation_digest=STRUCTURE_OBSERVATION.digest,
        postgres_major=16,
        declaration_schema=catalog.schema,
        declaration_scope=catalog.scope.value,
        complete_schemas=catalog.complete_schemas,
        product_code=(
            catalog.product_identity.catalog_product
            if catalog.product_identity is not None
            else ""
        ),
        product_version=(
            catalog.product_identity.catalog_version
            if catalog.product_identity is not None
            else ""
        ),
        comparator_id=comparator_id,
    )


def _descriptor_text_with_catalog(schema: str) -> str:
    base = BASE_PRODUCT_PATH.read_text(encoding="utf-8").replace(
        'schema = "ProductDeploymentSpec.v1"', f'schema = "{schema}"', 1
    )
    descriptor = (
        base
        + """

[database]
postgres_major = 16
expected_schemas = ["mod_deploy", "public"]
tablespaces = "none"

[[database.roles]]
name = "app_admin"
kind = "migration_owner"
inherit = true
login = true
bypassrls = true
member_of = []

[[database.catalogs]]
schema = "dotmac.product-database-catalog/v1"
path = "catalogs/product-database.json"
digest = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
scope = "product"
complete_schemas = ["mod_deploy", "public"]

[database.catalogs.product_identity]
descriptor_product = "dotmac_starter_mt"
catalog_product = "dotmac-starter-mt"
catalog_version = "0.1.0"
mapping_ref = "docs/adr/0070-deployment-is-a-stateless-versioned-foundation.md"
"""
    )
    return descriptor.replace(
        "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        PRODUCT_CATALOG.digest,
    )


def test_expressible_facts_match_but_structure_gaps_prevent_a_full_match() -> None:
    report = _compare()

    assert report.findings == ()
    assert not report.clean
    assert report.classification is DatabaseDriftClassification.UNMEASURABLE
    assert report.exit_code == 1
    assert {gap.code for gap in report.contract_gaps} == {
        DatabaseContractGapCode.TABLE_DECLARATION,
        DatabaseContractGapCode.COLUMN_DECLARATION,
    }


def test_v1_refuses_v2_catalog_fields_instead_of_partially_reading_them() -> None:
    with pytest.raises(SpecError, match="catalogs"):
        ProductDeploymentSpec.loads(_descriptor_text_with_catalog(SCHEMA_V1))

    with pytest.raises(SpecError, match="v1 cannot carry"):
        dataclasses.replace(
            _spec(),
            database=dataclasses.replace(_contract(), catalogs=(MODULE_CATALOG,)),
        )


def test_v2_database_refuses_to_leave_catalog_coordinates_implicit() -> None:
    without_catalog, _, _ = _descriptor_text_with_catalog(SCHEMA_V2).partition(
        "[[database.catalogs]]"
    )

    with pytest.raises(SpecError, match="requires at least one"):
        ProductDeploymentSpec.loads(without_catalog)


def test_v1_canonical_projection_omits_the_version_gated_catalog_field() -> None:
    spec = _spec()
    document = build_canonical_document(spec, refuse_resolved_material=False)

    database = document.content["descriptor"]["database"]
    assert document.content["descriptor_schema"] == SCHEMA_V1
    assert "catalogs" not in database


def test_v2_parses_and_digest_binds_catalog_coordinates() -> None:
    spec = ProductDeploymentSpec.loads(_descriptor_text_with_catalog(SCHEMA_V2))
    document = build_canonical_document(spec, refuse_resolved_material=False)

    assert spec.descriptor_schema == SCHEMA_V2
    assert spec.database is not None
    assert spec.database.catalogs == (PRODUCT_CATALOG,)
    assert document.content["descriptor_schema"] == SCHEMA_V2
    assert document.content["descriptor"]["database"]["catalogs"][0]["digest"] == (
        PRODUCT_CATALOG.digest
    )

    changed_catalog = dataclasses.replace(PRODUCT_CATALOG, digest="sha256:" + "e" * 64)
    changed = dataclasses.replace(
        spec,
        database=dataclasses.replace(spec.database, catalogs=(changed_catalog,)),
    )
    assert configuration_digest(changed) != configuration_digest(spec)

    assert PRODUCT_CATALOG.product_identity is not None
    changed_identity = dataclasses.replace(
        PRODUCT_CATALOG.product_identity, catalog_version="0.2.0"
    )
    changed_version = dataclasses.replace(
        spec,
        database=dataclasses.replace(
            spec.database,
            catalogs=(
                dataclasses.replace(PRODUCT_CATALOG, product_identity=changed_identity),
            ),
        ),
    )
    assert configuration_digest(changed_version) != configuration_digest(spec)


def test_v2_complete_product_verifier_witness_can_match() -> None:
    spec = dataclasses.replace(
        _spec(),
        descriptor_schema=SCHEMA_V2,
        database=dataclasses.replace(_contract(), catalogs=(PRODUCT_CATALOG,)),
    )
    binding = _binding(spec, PRODUCT_CATALOG)
    verifier = _RecordingVerifier(_verified_comparison(PRODUCT_CATALOG))
    witness = accept_database_structure_comparison(
        binding,
        PRODUCT_CATALOG,
        catalog_payload=PRODUCT_CATALOG_PAYLOAD,
        observation=STRUCTURE_OBSERVATION,
        observed_at="2026-08-31T05:44:36Z",
        verifier=verifier,
    )
    report = compare_database_contract(
        spec,
        _observe(),
        phase=DatabaseDriftPhase.POSTFLIGHT,
        catalog_binding=binding,
        structure_witnesses=(witness,),
    )

    assert verifier.calls[0]["declaration_bytes"] == PRODUCT_CATALOG_PAYLOAD
    assert report.clean
    assert report.matched_descriptor_digest == configuration_digest(spec)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("declaration_schema", "dotmac.module-database-catalog/v1", "schema differs"),
        ("declaration_scope", "module", "scope differs"),
        ("complete_schemas", ("mod_deploy",), "schema extent differs"),
        ("product_code", "another-product", "product code/version differs"),
        ("product_version", "9.9.9", "product code/version differs"),
    ),
)
def test_verified_declaration_identity_must_equal_product_coordinate(
    field: str, value: object, message: str
) -> None:
    spec = dataclasses.replace(
        _spec(),
        descriptor_schema=SCHEMA_V2,
        database=dataclasses.replace(_contract(), catalogs=(PRODUCT_CATALOG,)),
    )
    binding = _binding(spec, PRODUCT_CATALOG)
    result = dataclasses.replace(
        _verified_comparison(PRODUCT_CATALOG), **{field: value}
    )

    with pytest.raises(PreconditionFailed, match=message):
        accept_database_structure_comparison(
            binding,
            PRODUCT_CATALOG,
            catalog_payload=PRODUCT_CATALOG_PAYLOAD,
            observation=STRUCTURE_OBSERVATION,
            observed_at="2026-08-31T05:44:36Z",
            verifier=_RecordingVerifier(result),
        )


def test_catalog_sidecar_binds_v1_without_redefining_v1() -> None:
    spec = _spec()
    binding = _binding(spec)

    assert binding.descriptor_digest == configuration_digest(spec)
    assert binding.catalogs == (MODULE_CATALOG,)
    assert binding.sha256_digest() != binding.descriptor_digest


def test_catalog_sidecar_has_one_strict_canonical_round_trip() -> None:
    binding = _binding(_spec())

    parsed = DatabaseDescriptorCatalogBindingV1.from_json_bytes(
        binding.canonical_bytes(), expected_digest=binding.sha256_digest()
    )

    assert parsed == binding
    assert parsed.canonical_bytes() == binding.canonical_bytes()


def test_catalog_sidecar_refuses_noncanonical_or_wrongly_bound_bytes() -> None:
    binding = _binding(_spec())
    noncanonical = binding.canonical_bytes() + b"\n"

    with pytest.raises(SpecError, match="not canonical"):
        DatabaseDescriptorCatalogBindingV1.from_json_bytes(noncanonical)
    with pytest.raises(PreconditionFailed, match="expected_digest"):
        DatabaseDescriptorCatalogBindingV1.from_json_bytes(
            binding.canonical_bytes(), expected_digest="sha256:" + "1" * 64
        )


@pytest.mark.parametrize(
    "path",
    ("../product-database.json", "/srv/product-database.json", "catalog\\db.json"),
)
def test_catalog_coordinate_refuses_paths_outside_the_release(path: str) -> None:
    with pytest.raises(SpecError, match="path"):
        DatabaseCatalogCoordinateV1(
            schema=MODULE_CATALOG.schema,
            path=path,
            digest=MODULE_CATALOG.digest,
            scope=MODULE_CATALOG.scope,
            complete_schemas=MODULE_CATALOG.complete_schemas,
        )


@pytest.mark.parametrize(
    ("schema", "scope"),
    (
        ("dotmac.module-database-catalog/v1", DatabaseCatalogScope.PRODUCT),
        ("dotmac.product-database-catalog/v1", DatabaseCatalogScope.MODULE),
    ),
)
def test_kernel_catalog_schema_and_scope_cannot_contradict(
    schema: str, scope: DatabaseCatalogScope
) -> None:
    with pytest.raises(SpecError, match="requires scope"):
        DatabaseCatalogCoordinateV1(
            schema=schema,
            path="catalogs/contradiction.json",
            digest=MODULE_CATALOG.digest,
            scope=scope,
            complete_schemas=("mod_deploy",),
        )


def test_future_catalog_schema_requires_explicit_registration() -> None:
    with pytest.raises(SpecError, match="explicit schema/scope registration"):
        DatabaseCatalogCoordinateV1(
            schema="dotmac.product-database-catalog/v2",
            path="catalogs/future.json",
            digest=PRODUCT_CATALOG.digest,
            scope=DatabaseCatalogScope.PRODUCT,
            complete_schemas=("mod_deploy", "public"),
            product_identity=PRODUCT_CATALOG.product_identity,
        )


def test_product_catalog_alias_requires_a_declared_mapping() -> None:
    with pytest.raises(SpecError, match="mapping_ref"):
        DatabaseCatalogProductIdentityV1(
            descriptor_product="dotmac_starter_mt",
            catalog_product="dotmac-starter-mt",
            catalog_version="0.1.0",
        )


def test_product_catalog_identity_must_name_the_descriptor_product() -> None:
    wrong_identity = DatabaseCatalogProductIdentityV1(
        descriptor_product="dotmac_vendor_control_plane",
        catalog_product="dotmac-starter-mt",
        catalog_version="0.1.0",
        mapping_ref="docs/adr/0070-deployment-is-a-stateless-versioned-foundation.md",
    )
    wrong_catalog = dataclasses.replace(
        PRODUCT_CATALOG, product_identity=wrong_identity
    )

    with pytest.raises(SpecError, match="descriptor product exactly"):
        dataclasses.replace(
            _spec(),
            descriptor_schema=SCHEMA_V2,
            database=dataclasses.replace(_contract(), catalogs=(wrong_catalog,)),
        )


def test_structure_fact_keys_do_not_parse_quoted_identifier_punctuation() -> None:
    key = DatabaseStructureFactKeyV1(
        dimension=StructureFactDimension.COLUMN,
        schema="mod.deploy",
        table="plan:history",
        column="digest->canonical",
        attribute=DatabaseStructureFactAttribute.POSTGRES_TYPE,
    )

    assert '"schema":"mod.deploy"' in key.subject
    assert '"table":"plan:history"' in key.subject
    assert '"column":"digest->canonical"' in key.subject


def test_unknown_comparator_contract_cannot_mint_a_witness() -> None:
    binding = _binding(_spec())
    verifier = _RecordingVerifier(
        _verified_comparison(MODULE_CATALOG, comparator_id="invented/v1")
    )

    with pytest.raises(PreconditionFailed, match="unsupported.*comparator"):
        accept_database_structure_comparison(
            binding,
            MODULE_CATALOG,
            catalog_payload=MODULE_CATALOG_PAYLOAD,
            observation=STRUCTURE_OBSERVATION,
            observed_at="2026-08-31T05:44:36Z",
            verifier=verifier,
        )


def test_verifier_is_invoked_over_both_held_payloads() -> None:
    binding = _binding(_spec())
    verifier = _RecordingVerifier(_verified_comparison(MODULE_CATALOG))

    accept_database_structure_comparison(
        binding,
        MODULE_CATALOG,
        catalog_payload=MODULE_CATALOG_PAYLOAD,
        observation=STRUCTURE_OBSERVATION,
        observed_at="2026-08-31T05:44:36Z",
        verifier=verifier,
    )

    assert verifier.calls == [
        {
            "declaration_bytes": MODULE_CATALOG_PAYLOAD,
            "declaration_digest": MODULE_CATALOG.digest,
            "observation_bytes": OBSERVATION_PAYLOAD,
            "observation_digest": STRUCTURE_OBSERVATION.digest,
        }
    ]


def test_held_payload_digests_are_checked_before_verifier_dispatch() -> None:
    binding = _binding(_spec())
    verifier = _RecordingVerifier(_verified_comparison(MODULE_CATALOG))

    with pytest.raises(PreconditionFailed, match="catalogue bytes"):
        accept_database_structure_comparison(
            binding,
            MODULE_CATALOG,
            catalog_payload=b"different catalogue",
            observation=STRUCTURE_OBSERVATION,
            observed_at="2026-08-31T05:44:36Z",
            verifier=verifier,
        )
    assert verifier.calls == []


def test_structure_result_cannot_mix_postgres_major_observations() -> None:
    binding = _binding(_spec())
    pg_17_observation = dataclasses.replace(STRUCTURE_OBSERVATION, postgres_major=17)

    with pytest.raises(SpecError, match="different PostgreSQL majors"):
        dataclasses.replace(_structure_result(binding), observation=pg_17_observation)

    with pytest.raises(ValueError, match="different PostgreSQL majors"):
        _observe(structure_observation=pg_17_observation)


def test_complete_sidecar_coordinates_still_cannot_redefine_v1_as_a_match() -> None:
    spec = _spec()
    binding = _binding(spec, PRODUCT_CATALOG)

    report = compare_database_contract(
        spec,
        _observe(),
        phase=DatabaseDriftPhase.POSTFLIGHT,
        catalog_binding=binding,
    )

    assert not report.clean
    assert report.classification is DatabaseDriftClassification.UNMEASURABLE
    assert report.matched_descriptor_digest == ""
    assert DatabaseContractGapCode.DESCRIPTOR_CATALOG_BINDING in {
        gap.code for gap in report.contract_gaps
    }


def test_catalog_coordinate_without_observer_witness_stays_unmeasurable() -> None:
    spec = _spec()
    report = _compare(catalog_binding=_binding(spec))

    assert not report.clean
    assert report.classification is DatabaseDriftClassification.UNMEASURABLE
    assert any(
        "no accepted structural witness" in gap.detail for gap in report.contract_gaps
    )


def test_module_coordinate_cannot_claim_a_whole_descriptor_match() -> None:
    spec = _spec()
    binding = _binding(spec)

    report = compare_database_contract(
        spec,
        _observe(),
        phase=DatabaseDriftPhase.POSTFLIGHT,
        catalog_binding=binding,
    )

    assert not report.clean
    assert report.matched_descriptor_digest == ""
    assert report.classification is DatabaseDriftClassification.UNMEASURABLE
    assert any(
        gap.code is DatabaseContractGapCode.PRODUCT_STRUCTURE_COVERAGE
        and "public" in gap.detail
        for gap in report.contract_gaps
    )


def test_incomplete_live_extent_cannot_produce_a_structural_witness() -> None:
    binding = _binding(_spec())

    def refusing_verifier(
        *,
        declaration_bytes: bytes,
        declaration_digest: str,
        observation_bytes: bytes,
        observation_digest: str,
    ) -> _VerifiedComparison:
        del (
            declaration_bytes,
            declaration_digest,
            observation_bytes,
            observation_digest,
        )
        raise ValueError("observation does not cover every declared schema")

    with pytest.raises(PreconditionFailed, match="owning.*verifier refused"):
        accept_database_structure_comparison(
            binding,
            MODULE_CATALOG,
            catalog_payload=MODULE_CATALOG_PAYLOAD,
            observation=STRUCTURE_OBSERVATION,
            observed_at="2026-08-31T05:44:36Z",
            verifier=refusing_verifier,
        )


def test_changed_column_type_requires_both_set_difference_directions() -> None:
    spec = _spec()
    binding = _binding(spec)
    changed = (
        DatabaseStructureFindingV1(
            key=DatabaseStructureFactKeyV1(
                dimension=StructureFactDimension.COLUMN,
                schema="mod_deploy",
                table="deployment_plans",
                column="canonical_digest",
                attribute=DatabaseStructureFactAttribute.POSTGRES_TYPE,
            ),
            direction=StructureFactDirection.DECLARED_BUT_ABSENT,
            declared="pg_catalog.varchar(64)",
            observed="pg_catalog.varchar(128)",
        ),
        DatabaseStructureFindingV1(
            key=DatabaseStructureFactKeyV1(
                dimension=StructureFactDimension.COLUMN,
                schema="mod_deploy",
                table="deployment_plans",
                column="canonical_digest",
                attribute=DatabaseStructureFactAttribute.POSTGRES_TYPE,
            ),
            direction=StructureFactDirection.PRESENT_BUT_UNDECLARED,
            declared="pg_catalog.varchar(64)",
            observed="pg_catalog.varchar(128)",
        ),
    )
    with pytest.raises(SpecError, match="both set-difference directions"):
        _structure_result(binding, findings=changed[1:])

    result = _structure_result(binding, findings=changed)

    assert {finding.direction for finding in result.findings} == {
        StructureFactDirection.DECLARED_BUT_ABSENT,
        StructureFactDirection.PRESENT_BUT_UNDECLARED,
    }


def test_verified_typed_column_drifts_map_in_both_directions() -> None:
    spec = _spec()
    binding = _binding(spec)
    drifts = tuple(
        _VerifiedDrift(
            dimension="column",
            direction=direction,
            schema="mod_deploy",
            table="deployment_plans",
            column="canonical_digest",
            attribute="postgres_type",
            declared='{"formatted":"character varying(64)"}',
            observed='{"formatted":"character varying(128)"}',
        )
        for direction in ("declared_but_absent", "present_but_undeclared")
    )
    verifier = _RecordingVerifier(_verified_comparison(MODULE_CATALOG, drifts=drifts))
    witness = accept_database_structure_comparison(
        binding,
        MODULE_CATALOG,
        catalog_payload=MODULE_CATALOG_PAYLOAD,
        observation=STRUCTURE_OBSERVATION,
        observed_at="2026-08-31T05:44:36Z",
        verifier=verifier,
    )
    report = compare_database_contract(
        spec,
        _observe(),
        phase=DatabaseDriftPhase.POSTFLIGHT,
        catalog_binding=binding,
        structure_witnesses=(witness,),
    )

    typed = tuple(
        finding
        for finding in report.findings
        if finding.dimension is DatabaseFactDimension.COLUMN
    )
    assert {finding.direction for finding in typed} == {
        DatabaseFactDirection.DECLARED_BUT_ABSENT,
        DatabaseFactDirection.PRESENT_BUT_UNDECLARED,
    }


def test_structural_witness_is_bound_to_one_sidecar() -> None:
    spec = _spec()
    binding = _binding(spec)
    verifier = _RecordingVerifier(
        _verified_comparison(MODULE_CATALOG, declaration_digest="sha256:" + "e" * 64)
    )

    with pytest.raises(PreconditionFailed, match="different database catalogue"):
        accept_database_structure_comparison(
            binding,
            MODULE_CATALOG,
            catalog_payload=MODULE_CATALOG_PAYLOAD,
            observation=STRUCTURE_OBSERVATION,
            observed_at="2026-08-31T05:44:36Z",
            verifier=verifier,
        )


def test_declared_but_absent_is_reported_for_schema_role_and_head() -> None:
    catalog = dataclasses.replace(
        _catalog(),
        roles=tuple(role for role in _catalog().roles if role.name != "platform_api"),
        memberships=(),
        schemas=tuple(
            schema for schema in _catalog().schemas if schema != "mod_deploy"
        ),
        migration_heads=("kernel_0016",),
        effective_privileges=(),
    )

    report = _compare(catalog=catalog)
    missing = report.declared_but_absent

    assert any(
        finding.dimension is DatabaseFactDimension.SCHEMA
        and finding.subject == "mod_deploy"
        for finding in missing
    )
    assert any(
        finding.dimension is DatabaseFactDimension.ROLE
        and finding.subject == "platform_api"
        for finding in missing
    )
    assert any(
        finding.dimension is DatabaseFactDimension.MIGRATION_HEAD
        and finding.subject == "deploy_0001"
        for finding in missing
    )


def test_present_but_undeclared_catches_the_post_bootstrap_incident_shape() -> None:
    """The accepted descriptor still described the state BEFORE bootstrap.

    Production had ``mod_deploy``, a new head and the DELETE revocation.  All
    three are positive live facts missing from the declaration, and all three
    must be findings rather than evidence used to rewrite the declaration.
    """
    stale = dataclasses.replace(
        _contract(),
        expected_schemas=("public",),
        isolation=(),
    )

    report = _compare(
        contract=stale,
        expected_heads=("kernel_0016",),
    )
    extra = report.present_but_undeclared

    assert any(
        finding.dimension is DatabaseFactDimension.SCHEMA
        and finding.subject == "mod_deploy"
        for finding in extra
    )
    assert any(
        finding.dimension is DatabaseFactDimension.MIGRATION_HEAD
        and finding.subject == "deploy_0001"
        for finding in extra
    )
    assert any(
        finding.dimension is DatabaseFactDimension.ISOLATION
        and "platform_api" in finding.subject
        and "DELETE" in finding.subject
        and finding.observed == "holds=false"
        for finding in extra
    )


def test_a_value_change_is_two_facts_not_an_ambiguous_mismatch() -> None:
    catalog = dataclasses.replace(
        _catalog(),
        roles=tuple(
            dataclasses.replace(role, inherit=True)
            if role.name == "platform_api"
            else role
            for role in _catalog().roles
        ),
    )

    report = _compare(catalog=catalog)
    related = [
        finding
        for finding in report.findings
        if finding.dimension is DatabaseFactDimension.ROLE_ATTRIBUTE
        and finding.subject == "platform_api.inherit"
    ]

    assert {finding.direction for finding in related} == {
        DatabaseFactDirection.DECLARED_BUT_ABSENT,
        DatabaseFactDirection.PRESENT_BUT_UNDECLARED,
    }
    assert {finding.declared for finding in related} == {"false"}
    assert {finding.observed for finding in related} == {"true"}


@pytest.mark.parametrize(
    ("phase", "classification"),
    [
        (
            DatabaseDriftPhase.PREFLIGHT,
            DatabaseDriftClassification.PRECONDITION_DRIFT,
        ),
        (
            DatabaseDriftPhase.POSTFLIGHT,
            DatabaseDriftClassification.POSTCONDITION_FAILURE,
        ),
        (
            DatabaseDriftPhase.RECOVERY,
            DatabaseDriftClassification.RECOVERY_DRIFT,
        ),
    ],
)
def test_failure_classification_names_when_the_comparison_ran(
    phase: DatabaseDriftPhase, classification: DatabaseDriftClassification
) -> None:
    report = _compare(
        contract=dataclasses.replace(_contract(), expected_schemas=("public",)),
        phase=phase,
    )

    assert not report.clean
    assert report.classification is classification
    assert report.exit_code == 1


@pytest.mark.parametrize(
    ("declared_major", "observed_major", "dimension"),
    [
        (15, 16, DatabaseFactDimension.POSTGRES_MAJOR),
        (16, 16, None),
    ],
)
def test_postgres_major_is_compared(
    declared_major: int,
    observed_major: int,
    dimension: DatabaseFactDimension | None,
) -> None:
    report = compare_database_contract(
        _spec(contract=dataclasses.replace(_contract(), postgres_major=declared_major)),
        ObservedDatabaseState(
            postgres_major=observed_major,
            catalog=_catalog(),
            effective_privilege_universe=EffectivePrivilegeAuditUniverse(
                keys=frozenset(
                    EffectivePrivilegeKey.from_fact(fact)
                    for fact in _catalog().effective_privileges
                ),
                derivation=PrivilegeUniverseDerivation.CATALOG_DISCOVERY,
            ),
        ),
        phase=DatabaseDriftPhase.PREFLIGHT,
        exclusions=POSTGRES_SYSTEM_EXCLUSIONS,
    )

    dimensions = {finding.dimension for finding in report.findings}
    assert (DatabaseFactDimension.POSTGRES_MAJOR in dimensions) is (
        dimension is not None
    )


def test_tablespace_posture_compares_without_inventing_a_mapping() -> None:
    observed = dataclasses.replace(
        _catalog(),
        tablespaces=TablespaceDecision(
            kind="mapped", mapping=(("fast", "/srv/postgres/fast"),)
        ),
    )
    report = _compare(catalog=observed)

    assert {
        finding.direction
        for finding in report.findings
        if finding.dimension is DatabaseFactDimension.TABLESPACES
    } == {
        DatabaseFactDirection.DECLARED_BUT_ABSENT,
        DatabaseFactDirection.PRESENT_BUT_UNDECLARED,
    }
    assert not any("/srv/postgres/fast" in str(finding) for finding in report.findings)


def test_exclusions_are_typed_explicit_and_do_not_learn_an_application_schema() -> None:
    observed = dataclasses.replace(
        _catalog(), schemas=(*_catalog().schemas, "mod_unapproved", "vendor_shadow")
    )
    exclusions = DatabaseDriftExclusions(
        schema_names=(
            NameExclusion(name="vendor_shadow", reason="owned by the test harness"),
        ),
        schema_prefixes=(
            PrefixExclusion(prefix="pg_temp_", reason="PostgreSQL temporary schema"),
        ),
    )

    report = _compare(catalog=observed, exclusions=exclusions)
    extra_schemas = {
        finding.subject
        for finding in report.present_but_undeclared
        if finding.dimension is DatabaseFactDimension.SCHEMA
    }

    assert "vendor_shadow" not in extra_schemas
    assert "mod_unapproved" in extra_schemas
    # No inferred "looks system-ish" rule: only the supplied names/prefixes
    # disappear from the comparison.
    assert "pg_catalog" in extra_schemas


def test_an_exclusion_without_a_reason_is_refused() -> None:
    with pytest.raises(ValueError, match="reason"):
        NameExclusion(name="vendor_shadow", reason="")


def test_an_effective_privilege_exclusion_is_exact_not_role_wide() -> None:
    catalog = dataclasses.replace(
        _catalog(),
        effective_privileges=(
            dataclasses.replace(_catalog().effective_privileges[0], holds=True),
            EffectivePrivilegeFact(
                role="platform_api",
                scope="table",
                identity="public.licence_delivery_targets",
                privilege="SELECT",
                holds=True,
            ),
        ),
    )
    exclusions = dataclasses.replace(
        POSTGRES_SYSTEM_EXCLUSIONS,
        effective_privileges=(
            EffectivePrivilegeExclusion(
                key=EffectivePrivilegeKey(
                    role="platform_api",
                    scope="table",
                    identity="public.licence_delivery_targets",
                    privilege="SELECT",
                ),
                reason="outside this descriptor revision's recovery scope",
            ),
        ),
    )

    report = _compare(catalog=catalog, exclusions=exclusions)

    assert not any("SELECT" in finding.subject for finding in report.findings)
    assert any("DELETE" in finding.subject for finding in report.findings)


def test_comparison_is_read_only_over_the_observed_snapshot() -> None:
    observation = _observe()
    before = observation.catalog

    _ = compare_database_contract(
        _spec(),
        observation,
        phase=DatabaseDriftPhase.RECOVERY,
        exclusions=POSTGRES_SYSTEM_EXCLUSIONS,
    )

    assert observation.catalog == before


def test_missing_privilege_audit_universe_is_unmeasurable_not_clean() -> None:
    report = compare_database_contract(
        _spec(),
        ObservedDatabaseState(postgres_major=16, catalog=_catalog()),
        phase=DatabaseDriftPhase.PREFLIGHT,
        exclusions=POSTGRES_SYSTEM_EXCLUSIONS,
    )

    assert report.classification is DatabaseDriftClassification.UNMEASURABLE
    assert not report.clean
    assert report.exit_code == 1
    assert report.matched_descriptor_digest == ""
    assert any("independently derived" in issue for issue in report.measurement_issues)


def test_incomplete_privilege_observation_is_unmeasurable() -> None:
    expected_key = EffectivePrivilegeKey.from_fact(_catalog().effective_privileges[0])
    incomplete = dataclasses.replace(_catalog(), effective_privileges=())
    report = compare_database_contract(
        _spec(),
        ObservedDatabaseState(
            postgres_major=16,
            catalog=incomplete,
            effective_privilege_universe=EffectivePrivilegeAuditUniverse(
                keys=frozenset({expected_key}),
                derivation=PrivilegeUniverseDerivation.CATALOG_DISCOVERY,
            ),
        ),
        phase=DatabaseDriftPhase.PREFLIGHT,
        exclusions=POSTGRES_SYSTEM_EXCLUSIONS,
    )

    assert report.classification is DatabaseDriftClassification.UNMEASURABLE
    assert any("supplied no" in issue for issue in report.measurement_issues)


def test_duplicate_privilege_answers_are_unmeasurable() -> None:
    duplicated = dataclasses.replace(
        _catalog(),
        effective_privileges=(
            *_catalog().effective_privileges,
            _catalog().effective_privileges[0],
        ),
    )
    report = _compare(catalog=duplicated)

    assert report.classification is DatabaseDriftClassification.UNMEASURABLE
    assert any("exactly one" in issue for issue in report.measurement_issues)


def test_report_binds_the_descriptor_but_does_not_claim_it_matched_across_gaps() -> (
    None
):
    spec = _spec()
    report = compare_database_contract(
        spec,
        _observe(),
        phase=DatabaseDriftPhase.POSTFLIGHT,
        exclusions=POSTGRES_SYSTEM_EXCLUSIONS,
    )

    # The digest the DEPLOYMENT carries, not one re-derived here. The strict
    # `to_canonical_document()` refuses a descriptor holding resolved material,
    # and this repository's own product.toml holds an in-container
    # `--host 0.0.0.0`, so asserting against it would compare the report to a
    # document that cannot be built for the descriptor under test.
    assert report.descriptor_digest == configuration_digest(spec)
    assert report.matched_descriptor_digest == ""
