from __future__ import annotations

from dataclasses import dataclass

import pytest
from dotmac_billing.authority import (
    BillingPlane,
    CommercialAuthority,
    _reset_authority_for_tests,
    bind_commercial_authority,
)
from dotmac_billing.errors import BillingRuleViolation


@dataclass(frozen=True, slots=True)
class _Repository:
    plane: BillingPlane


def _tenant_repository() -> _Repository:
    return _Repository(BillingPlane.TENANT)


def _platform_repository() -> _Repository:
    return _Repository(BillingPlane.PLATFORM)


@pytest.fixture(autouse=True)
def reset_binding() -> None:
    _reset_authority_for_tests()


def test_two_simultaneously_active_commercial_authorities_are_refused() -> None:
    bind_commercial_authority(
        CommercialAuthority.INTERNAL, tenant_repository_factory=_tenant_repository
    )
    with pytest.raises(BillingRuleViolation, match="already bound"):
        bind_commercial_authority(
            CommercialAuthority.EXTERNAL_FINANCE,
            tenant_repository_factory=_tenant_repository,
        )


def test_rebinding_the_same_authority_is_also_refused() -> None:
    bind_commercial_authority(
        CommercialAuthority.INTERNAL,
        platform_repository_factory=_platform_repository,
    )
    with pytest.raises(BillingRuleViolation, match="already bound"):
        bind_commercial_authority(
            CommercialAuthority.INTERNAL,
            platform_repository_factory=_platform_repository,
        )


def test_exactly_one_plane_must_be_bound() -> None:
    with pytest.raises(BillingRuleViolation, match="exactly one"):
        bind_commercial_authority(CommercialAuthority.INTERNAL)
    with pytest.raises(BillingRuleViolation, match="exactly one"):
        bind_commercial_authority(
            CommercialAuthority.INTERNAL,
            tenant_repository_factory=_tenant_repository,
            platform_repository_factory=_platform_repository,
        )


def test_a_repository_factory_cannot_smuggle_the_wrong_plane() -> None:
    with pytest.raises(BillingRuleViolation, match="declared plane"):
        bind_commercial_authority(
            CommercialAuthority.INTERNAL,
            platform_repository_factory=_tenant_repository,
        )
