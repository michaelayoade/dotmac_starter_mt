"""Pure contract tests for additive permission provisioning plans.

The kernel plans against storage-neutral observed state.  A product migration
adapter may translate the returned inserts into its own schema, but the planner
itself performs no I/O and has no subtractive operation.
"""

from __future__ import annotations

import pytest
from dotmac_kernel import (
    FeatureManifest,
    PermissionPlan,
    PermissionPlanError,
    PermissionSpec,
    PermissionState,
    RoleDefinition,
    RoleGrant,
    RoleGrantProfile,
)


def _manifest(name: str, *permissions: PermissionSpec) -> FeatureManifest:
    return FeatureManifest(name=name, permissions=permissions)


def test_plan_keeps_module_definitions_separate_from_assembly_role_grants() -> None:
    plan = PermissionPlan.from_manifests(
        (
            _manifest(
                "expense",
                PermissionSpec("expense:reports.read", description="Read reports"),
                PermissionSpec("expense.claims.approve", description="Approve claims"),
                PermissionSpec("expense.operator.repair", description="Repair drift"),
            ),
        ),
        (
            RoleGrantProfile(
                code="erp.finance-baseline",
                grants=(
                    RoleGrant("finance_manager", "expense:reports.read"),
                    RoleGrant("finance_director", "expense.claims.approve"),
                ),
            ),
        ),
        (
            RoleDefinition("finance_director"),
            RoleDefinition("finance_manager"),
        ),
    )

    assert {definition.code for definition in plan.definitions} == {
        "expense:reports.read",
        "expense.claims.approve",
        "expense.operator.repair",
    }
    assert plan.grants == (
        RoleGrant("finance_director", "expense.claims.approve"),
        RoleGrant("finance_manager", "expense:reports.read"),
    )
    # A declared permission with no baseline grant is valid: operators may
    # assign it locally without the assembly inventing a product role for it.
    assert "expense.operator.repair" not in {
        grant.permission_code for grant in plan.grants
    }


def test_plan_input_order_does_not_change_its_normalized_result() -> None:
    manifests = (
        _manifest("b", PermissionSpec("b.read")),
        _manifest("a", PermissionSpec("a.read")),
    )
    profiles = (
        RoleGrantProfile("z", (RoleGrant("z_role", "b.read"),)),
        RoleGrantProfile("a", (RoleGrant("a_role", "a.read"),)),
    )
    roles = (RoleDefinition("z_role"), RoleDefinition("a_role"))

    forward = PermissionPlan.from_manifests(manifests, profiles, roles)
    reverse = PermissionPlan.from_manifests(
        tuple(reversed(manifests)),
        tuple(reversed(profiles)),
        tuple(reversed(roles)),
    )

    assert forward == reverse
    assert forward.digest == reverse.digest
    assert len(forward.digest) == 64

    changed_version = PermissionPlan.from_manifests(
        manifests,
        (
            RoleGrantProfile("z", (RoleGrant("z_role", "b.read"),), version=2),
            profiles[1],
        ),
        roles,
    )
    assert changed_version.digest != forward.digest


@pytest.mark.parametrize(
    ("value", "constructor"),
    [
        ("", lambda value: RoleGrant(value, "permission.read")),
        ("", lambda value: RoleGrant("role", value)),
        ("", lambda value: RoleGrantProfile(value)),
        (" ", lambda value: RoleGrantProfile(value)),
    ],
)
def test_role_and_permission_keys_are_opaque_but_non_empty(value, constructor) -> None:
    with pytest.raises(ValueError):
        constructor(value)

    # No grammar, separator, case, or whitespace normalization belongs to the
    # reusable contract.  Product adapters own those policies.
    opaque = RoleGrant(" Finance:Lead ", "Expense.Claims/Approve")
    assert opaque.role_code == " Finance:Lead "
    assert opaque.permission_code == "Expense.Claims/Approve"


def test_role_grant_profile_version_must_be_positive() -> None:
    with pytest.raises(ValueError, match="version must be positive"):
        RoleGrantProfile("erp.baseline", version=0)


def test_profile_may_reference_only_a_manifest_declared_permission() -> None:
    with pytest.raises(PermissionPlanError, match="undeclared permission"):
        PermissionPlan.from_manifests(
            (_manifest("expense", PermissionSpec("expense.read")),),
            (RoleGrantProfile("erp.baseline", (RoleGrant("admin", "expense.write"),)),),
            (RoleDefinition("admin"),),
        )


def test_profile_may_reference_only_an_assembly_declared_role() -> None:
    with pytest.raises(PermissionPlanError, match="undeclared role"):
        PermissionPlan.from_manifests(
            (_manifest("expense", PermissionSpec("expense.read")),),
            (
                RoleGrantProfile(
                    "erp.baseline", (RoleGrant("invented", "expense.read"),)
                ),
            ),
        )


def test_duplicate_role_definition_fails_closed() -> None:
    with pytest.raises(PermissionPlanError, match="duplicate role definition"):
        PermissionPlan.from_manifests(
            (_manifest("expense", PermissionSpec("expense.read")),),
            (),
            (RoleDefinition("finance"), RoleDefinition("finance")),
        )


def test_duplicate_profile_code_fails_closed() -> None:
    with pytest.raises(PermissionPlanError, match="profile code"):
        PermissionPlan.from_manifests(
            (_manifest("expense", PermissionSpec("expense.read")),),
            (
                RoleGrantProfile("erp", (RoleGrant("admin", "expense.read"),)),
                RoleGrantProfile("erp", (RoleGrant("auditor", "expense.read"),)),
            ),
            (RoleDefinition("admin"), RoleDefinition("auditor")),
        )


def test_two_profiles_cannot_both_own_the_same_baseline_grant() -> None:
    grant = RoleGrant("admin", "expense.read")
    with pytest.raises(PermissionPlanError, match="both profiles"):
        PermissionPlan.from_manifests(
            (_manifest("expense", PermissionSpec("expense.read")),),
            (RoleGrantProfile("one", (grant,)), RoleGrantProfile("two", (grant,))),
            (RoleDefinition("admin"),),
        )


def test_diff_is_additive_preserves_unknown_state_and_is_idempotent() -> None:
    plan = PermissionPlan.from_manifests(
        (
            _manifest(
                "expense",
                PermissionSpec("expense.read", description="Desired description"),
                PermissionSpec("expense.approve", description="Approve"),
            ),
        ),
        (
            RoleGrantProfile(
                "erp.baseline",
                (
                    RoleGrant("finance", "expense.read"),
                    RoleGrant("finance", "expense.approve"),
                ),
            ),
        ),
        (RoleDefinition("finance"), RoleDefinition("auditor")),
    )
    operator_grant = RoleGrant("auditor", "operator.local")
    observed = PermissionState(
        active_permission_codes=frozenset({"expense.read", "operator.local"}),
        active_role_codes=frozenset({"finance", "auditor"}),
        grants=frozenset(
            {
                RoleGrant("finance", "expense.read"),
                operator_grant,
            }
        ),
    )

    diff = plan.diff(observed)

    assert [item.code for item in diff.permission_inserts] == ["expense.approve"]
    # The existing expense.read row is not updated, so a product adapter cannot
    # overwrite its current description through this plan.
    assert "expense.read" not in {item.code for item in diff.permission_inserts}
    assert diff.grant_inserts == (RoleGrant("finance", "expense.approve"),)
    assert diff.missing_roles == ()
    assert diff.conflicts == ()
    assert diff.preserved_permission_codes == frozenset({"operator.local"})
    assert diff.preserved_role_codes == frozenset()
    assert diff.preserved_grants == frozenset({operator_grant})

    applied = PermissionState(
        active_permission_codes=(
            observed.active_permission_codes
            | frozenset(item.code for item in diff.permission_inserts)
        ),
        active_role_codes=observed.active_role_codes,
        grants=observed.grants | frozenset(diff.grant_inserts),
    )
    second = plan.diff(applied)
    assert second.permission_inserts == ()
    assert second.role_inserts == ()
    assert second.grant_inserts == ()
    assert second.conflicts == ()


def test_inactive_desired_permission_is_a_conflict_and_is_not_reactivated() -> None:
    plan = PermissionPlan.from_manifests(
        (_manifest("expense", PermissionSpec("expense.read")),),
        (RoleGrantProfile("erp.baseline", (RoleGrant("finance", "expense.read"),)),),
        (RoleDefinition("finance"),),
    )

    diff = plan.diff(
        PermissionState(
            inactive_permission_codes=frozenset({"expense.read"}),
            active_role_codes=frozenset({"finance"}),
        )
    )

    assert diff.permission_inserts == ()
    assert diff.grant_inserts == ()
    assert [(item.kind, item.subject) for item in diff.conflicts] == [
        ("inactive_permission", "expense.read")
    ]


def test_missing_role_is_an_explicit_conflict_and_is_never_created() -> None:
    plan = PermissionPlan.from_manifests(
        (_manifest("expense", PermissionSpec("expense.read")),),
        (RoleGrantProfile("erp.baseline", (RoleGrant("finance", "expense.read"),)),),
        (RoleDefinition("finance"),),
    )

    diff = plan.diff(PermissionState())

    assert diff.missing_roles == ("finance",)
    assert diff.grant_inserts == ()
    assert [(item.kind, item.subject) for item in diff.conflicts] == [
        ("missing_role", "finance")
    ]


def test_missing_role_is_inserted_only_when_the_assembly_explicitly_owns_it() -> None:
    plan = PermissionPlan.from_manifests(
        (_manifest("expense", PermissionSpec("expense.read")),),
        (RoleGrantProfile("erp.baseline", (RoleGrant("finance", "expense.read"),)),),
        (
            RoleDefinition(
                "finance",
                description="Finance baseline",
                create_if_missing=True,
            ),
        ),
    )

    diff = plan.diff(PermissionState())

    assert diff.role_inserts == (
        RoleDefinition(
            "finance",
            description="Finance baseline",
            create_if_missing=True,
        ),
    )
    assert diff.missing_roles == ()
    assert diff.conflicts == ()
    assert diff.grant_inserts == (RoleGrant("finance", "expense.read"),)


def test_inactive_desired_role_is_a_conflict_and_receives_no_grant() -> None:
    plan = PermissionPlan.from_manifests(
        (_manifest("expense", PermissionSpec("expense.read")),),
        (RoleGrantProfile("erp.baseline", (RoleGrant("finance", "expense.read"),)),),
        (RoleDefinition("finance"),),
    )

    diff = plan.diff(
        PermissionState(
            active_permission_codes=frozenset({"expense.read"}),
            inactive_role_codes=frozenset({"finance"}),
        )
    )

    assert diff.missing_roles == ()
    assert diff.grant_inserts == ()
    assert [(item.kind, item.subject) for item in diff.conflicts] == [
        ("inactive_role", "finance")
    ]


def test_observed_permission_cannot_be_both_active_and_inactive() -> None:
    with pytest.raises(ValueError, match="both active and inactive"):
        PermissionState(
            active_permission_codes=frozenset({"expense.read"}),
            inactive_permission_codes=frozenset({"expense.read"}),
        )


def test_observed_role_cannot_be_both_active_and_inactive() -> None:
    with pytest.raises(ValueError, match="both active and inactive"):
        PermissionState(
            active_role_codes=frozenset({"finance"}),
            inactive_role_codes=frozenset({"finance"}),
        )
