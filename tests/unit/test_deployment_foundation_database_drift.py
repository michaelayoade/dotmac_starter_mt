"""Database declarations are compared with live facts in both directions.

The Platform CP bootstrap incident is the sensitivity case: the accepted
descriptor omitted a schema, migration heads and a revocation that were all
present in production.  A declared-only validator cannot see that shape; exact
set comparison must report the live facts that the descriptor does not name.
"""

from __future__ import annotations

import dataclasses
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
from dotmac_deployment_foundation.recovery import (
    CatalogEvidence,
    EffectivePrivilegeFact,
    MembershipFact,
    RoleFact,
    TablespaceDecision,
)
from dotmac_deployment_foundation.render.compose import configuration_digest
from dotmac_deployment_foundation.spec import (
    DatabaseContract,
    DatabaseRole,
    IsolationInvariant,
    ProductDeploymentSpec,
)


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


BASE_SPEC = ProductDeploymentSpec.load(
    Path(__file__).resolve().parents[2] / "deploy" / "product.toml"
)


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


def _observe(catalog: CatalogEvidence | None = None) -> ObservedDatabaseState:
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
    )


def _compare(
    *,
    contract: DatabaseContract | None = None,
    catalog: CatalogEvidence | None = None,
    expected_heads: tuple[str, ...] = ("kernel_0016", "deploy_0001"),
    phase: DatabaseDriftPhase = DatabaseDriftPhase.PREFLIGHT,
    exclusions: DatabaseDriftExclusions = POSTGRES_SYSTEM_EXCLUSIONS,
) -> DatabaseContractDriftReport:
    return compare_database_contract(
        _spec(contract=contract, expected_heads=expected_heads),
        _observe(catalog),
        phase=phase,
        exclusions=exclusions,
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
