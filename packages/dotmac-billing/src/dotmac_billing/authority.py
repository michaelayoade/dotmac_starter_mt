"""Exactly-one commercial authority binding."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from dotmac_billing.errors import BillingRuleViolation


class CommercialAuthority(str, Enum):
    INTERNAL = "internal"
    PROVIDER_OWNED = "provider_owned"
    EXTERNAL_FINANCE = "external_finance"


class BillingPlane(str, Enum):
    TENANT = "tenant"
    PLATFORM = "platform"


@runtime_checkable
class BillingRepository(Protocol):
    """Typed assembly repository descriptor for one persistence plane."""

    @property
    def plane(self) -> BillingPlane: ...


class RepositoryFactory(Protocol):
    def __call__(self) -> BillingRepository: ...


@dataclass(frozen=True, slots=True)
class AuthorityBinding:
    authority: CommercialAuthority
    plane: BillingPlane
    repository_factory: RepositoryFactory


_binding: AuthorityBinding | None = None


def bind_commercial_authority(
    authority: CommercialAuthority,
    *,
    tenant_repository_factory: RepositoryFactory | None = None,
    platform_repository_factory: RepositoryFactory | None = None,
) -> AuthorityBinding:
    global _binding
    if _binding is not None:
        raise BillingRuleViolation(
            "duplicate_commercial_authority",
            "a commercial authority is already bound",
            existing=_binding.authority.value,
            attempted=authority.value,
        )
    supplied: tuple[tuple[BillingPlane, RepositoryFactory], ...] = tuple(
        (plane, factory)
        for plane, factory in (
            (BillingPlane.TENANT, tenant_repository_factory),
            (BillingPlane.PLATFORM, platform_repository_factory),
        )
        if factory is not None
    )
    if len(supplied) != 1:
        raise BillingRuleViolation(
            "ambiguous_plane",
            "bind exactly one tenant or platform repository factory",
        )
    plane, factory = supplied[0]
    repository = factory()
    if not isinstance(repository, BillingRepository) or repository.plane is not plane:
        raise BillingRuleViolation(
            "repository_plane_mismatch",
            "the bound repository factory must return the declared plane",
            declared=plane.value,
        )
    _binding = AuthorityBinding(authority, plane, factory)
    return _binding


def commercial_authority() -> AuthorityBinding:
    if _binding is None:
        raise BillingRuleViolation(
            "commercial_authority_unbound", "commercial authority is not bound"
        )
    return _binding


def _reset_authority_for_tests() -> None:
    global _binding
    _binding = None


__all__ = [
    "AuthorityBinding",
    "BillingPlane",
    "BillingRepository",
    "CommercialAuthority",
    "bind_commercial_authority",
    "commercial_authority",
]
