"""Portable contract canaries; authored before implementation, NOT RUN."""

import dataclasses
import inspect

import pytest
from dotmac_kernel.identity_provisioning import (
    BootstrapPolicyV1,
    BootstrapRoleV1,
    BootstrapTenantV1,
    bootstrap_tenant,
)


def test_command_has_no_material_or_authority_input():
    assert [field.name for field in dataclasses.fields(BootstrapTenantV1)] == [
        "slug",
        "name",
        "owner_email",
    ]
    assert list(inspect.signature(bootstrap_tenant).parameters) == [
        "db",
        "command",
        "password",
    ]
    assert (
        inspect.signature(bootstrap_tenant).parameters["password"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )


@pytest.mark.parametrize(
    "pairs", [(), (("", "offline"),), (("offline", ""),), (("x", "y"), ("x", "y"))]
)
def test_missing_or_malformed_paired_role_policy_refuses(pairs):
    with pytest.raises((TypeError, ValueError)):
        BootstrapPolicyV1(pairs, (BootstrapRoleV1("custom", "Custom"),), "custom", True)


@pytest.mark.parametrize(
    "roles,initial",
    [
        ((), "x"),
        ((BootstrapRoleV1("x", "X"),), "y"),
        ((BootstrapRoleV1("x", "X"),) * 2, "x"),
    ],
)
def test_policy_cannot_invent_initial_role(roles, initial):
    with pytest.raises((TypeError, ValueError)):
        BootstrapPolicyV1((("login", "effective"),), roles, initial, True)


def test_custom_business_values_remain_explicit_and_immutable():
    policy = BootstrapPolicyV1(
        (("custom_login", "custom_offline"),),
        (BootstrapRoleV1("custom_owner", "Custom Owner"),),
        "custom_owner",
        False,
    )
    assert policy.initial_role_slug == "custom_owner"
    assert policy.tenant_is_active is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.tenant_is_active = True
