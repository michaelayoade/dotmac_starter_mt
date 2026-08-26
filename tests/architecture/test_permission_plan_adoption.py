"""The reference assembly shadows today's authorization without changing it."""

from __future__ import annotations

from dotmac_kernel import PermissionPlan, RoleDefinition, RoleGrant

from app.assembly import assembly

EXPECTED_STARTER_ADMIN_PERMISSIONS = frozenset(
    {
        "web.portal.staff.access",
        "rbac.roles.read",
        "rbac.roles.manage",
        "rbac.grants.manage",
        "rbac.audit.read",
        "template_studio.templates.read",
        "template_studio.templates.manage",
        "template_studio.templates.publish",
        "template_studio.templates.render",
    }
)


def test_starter_admin_profile_is_explicit_exact_and_non_vacuous() -> None:
    assert EXPECTED_STARTER_ADMIN_PERMISSIONS
    assert len(assembly.role_grant_profiles) == 1
    assert assembly.role_definitions == (RoleDefinition("admin"),)

    profile = assembly.role_grant_profiles[0]
    assert profile.code == "starter.admin"
    assert profile.version == 1
    assert set(profile.grants) == {
        RoleGrant("admin", permission)
        for permission in EXPECTED_STARTER_ADMIN_PERMISSIONS
    }


def test_starter_profile_exactly_shadows_current_default_role_authorization() -> None:
    plan = PermissionPlan.from_manifests(
        assembly.modules,
        assembly.role_grant_profiles,
        assembly.role_definitions,
    )
    legacy_grants = {
        RoleGrant(role, permission.code)
        for manifest in assembly.modules
        for permission in manifest.permissions
        for role in permission.default_roles
    }

    assert legacy_grants
    assert set(plan.grants) == legacy_grants
    assert {grant.permission_code for grant in plan.grants} == (
        EXPECTED_STARTER_ADMIN_PERMISSIONS
    )
