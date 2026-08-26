"""Storage-neutral, additive permission provisioning plans.

Modules own permission definitions.  A product assembly separately owns the
baseline role mappings for the roles in that product.  This module compiles
those two inputs into a deterministic plan and compares it with observed state;
it imports no ORM, web framework, or persistence adapter and performs no I/O.

Stage 1 deliberately has no delete, revoke, rename, reactivate, or
description-update operation.  Existing state outside the baseline is reported
as preserved. A role may be inserted only when its assembly-owned definition
explicitly authorizes creation; every other missing role, and any inactive
desired role or permission, is an explicit conflict.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from dotmac_kernel.permissions import PermissionCatalogue

if TYPE_CHECKING:  # avoid a runtime cycle through manifest definitions
    from dotmac_kernel.modules import AnyManifest

PERMISSION_PLAN_SCHEMA_VERSION = 1

PermissionConflictKind = Literal[
    "inactive_permission",
    "inactive_role",
    "missing_role",
]


class PermissionPlanError(ValueError):
    """The module declarations and assembly role profiles cannot form a plan."""


def _require_non_empty(value: str, field_name: str) -> None:
    # Codes and keys are deliberately opaque.  In particular, do not strip,
    # case-fold, split, or impose a separator grammar here.
    if value.strip() == "":
        raise ValueError(f"{field_name} must be non-empty")


@dataclass(frozen=True, slots=True, order=True)
class PermissionDefinition:
    """One module-owned permission catalogue definition."""

    code: str
    description: str
    owner: str

    def __post_init__(self) -> None:
        _require_non_empty(self.code, "permission code")
        _require_non_empty(self.owner, "permission owner")


@dataclass(frozen=True, slots=True, order=True)
class RoleGrant:
    """One assembly-owned baseline role-to-permission mapping."""

    role_code: str
    permission_code: str

    def __post_init__(self) -> None:
        _require_non_empty(self.role_code, "role code")
        _require_non_empty(self.permission_code, "permission code")


@dataclass(frozen=True, slots=True, order=True)
class RoleDefinition:
    """One product-owned role and its explicit missing-row policy."""

    code: str
    description: str = ""
    create_if_missing: bool = False

    def __post_init__(self) -> None:
        _require_non_empty(self.code, "role code")


@dataclass(frozen=True, slots=True)
class RoleGrantProfile:
    """A named product baseline; modules never declare product role keys."""

    code: str
    grants: Sequence[RoleGrant] = ()
    version: int = 1

    def __post_init__(self) -> None:
        _require_non_empty(self.code, "role grant profile code")
        if self.version < 1:
            raise ValueError("role grant profile version must be positive")
        grants = tuple(sorted(self.grants))
        if len(grants) != len(set(grants)):
            raise PermissionPlanError(
                f"role grant profile {self.code!r} contains a duplicate grant"
            )
        object.__setattr__(self, "grants", grants)


@dataclass(frozen=True, slots=True, order=True)
class PermissionPlanConflict:
    """A condition Stage 1 refuses to repair implicitly."""

    kind: PermissionConflictKind
    subject: str

    def __post_init__(self) -> None:
        _require_non_empty(self.subject, "permission plan conflict subject")


@dataclass(frozen=True, slots=True)
class PermissionState:
    """Storage-neutral facts observed by a product persistence adapter."""

    active_permission_codes: frozenset[str] = frozenset()
    inactive_permission_codes: frozenset[str] = frozenset()
    active_role_codes: frozenset[str] = frozenset()
    inactive_role_codes: frozenset[str] = frozenset()
    grants: frozenset[RoleGrant] = frozenset()

    def __post_init__(self) -> None:
        active = frozenset(self.active_permission_codes)
        inactive = frozenset(self.inactive_permission_codes)
        active_roles = frozenset(self.active_role_codes)
        inactive_roles = frozenset(self.inactive_role_codes)
        grants = frozenset(self.grants)
        overlap = active & inactive
        if overlap:
            listed = ", ".join(repr(code) for code in sorted(overlap))
            raise ValueError(
                f"permission code(s) cannot be both active and inactive: {listed}"
            )
        for code in (*active, *inactive):
            _require_non_empty(code, "observed permission code")
        role_overlap = active_roles & inactive_roles
        if role_overlap:
            listed = ", ".join(repr(code) for code in sorted(role_overlap))
            raise ValueError(
                f"role code(s) cannot be both active and inactive: {listed}"
            )
        for role_code in (*active_roles, *inactive_roles):
            _require_non_empty(role_code, "observed role code")
        object.__setattr__(self, "active_permission_codes", active)
        object.__setattr__(self, "inactive_permission_codes", inactive)
        object.__setattr__(self, "active_role_codes", active_roles)
        object.__setattr__(self, "inactive_role_codes", inactive_roles)
        object.__setattr__(self, "grants", grants)


@dataclass(frozen=True, slots=True)
class PermissionPlanDiff:
    """Additions, conflicts, and untouched state for one observed snapshot."""

    permission_inserts: tuple[PermissionDefinition, ...]
    role_inserts: tuple[RoleDefinition, ...]
    grant_inserts: tuple[RoleGrant, ...]
    missing_roles: tuple[str, ...]
    conflicts: tuple[PermissionPlanConflict, ...]
    preserved_permission_codes: frozenset[str]
    preserved_role_codes: frozenset[str]
    preserved_grants: frozenset[RoleGrant]

    @property
    def is_noop(self) -> bool:
        """True when the desired additions are already present and conflict-free."""

        return not (
            self.permission_inserts
            or self.role_inserts
            or self.grant_inserts
            or self.conflicts
            or self.missing_roles
        )


@dataclass(frozen=True, slots=True)
class PermissionPlan:
    """Deterministic definitions and assembly-owned baseline role grants."""

    definitions: tuple[PermissionDefinition, ...]
    roles: tuple[RoleDefinition, ...]
    profiles: tuple[RoleGrantProfile, ...]
    grants: tuple[RoleGrant, ...]

    @property
    def digest(self) -> str:
        """Stable plan identity for previews, evidence, and drift comparison."""

        document = {
            "schema_version": PERMISSION_PLAN_SCHEMA_VERSION,
            "definitions": [
                {
                    "code": item.code,
                    "description": item.description,
                    "owner": item.owner,
                }
                for item in self.definitions
            ],
            "roles": [
                {
                    "code": role.code,
                    "description": role.description,
                    "create_if_missing": role.create_if_missing,
                }
                for role in self.roles
            ],
            "profiles": [
                {
                    "code": profile.code,
                    "version": profile.version,
                    "grants": [
                        {
                            "role_code": grant.role_code,
                            "permission_code": grant.permission_code,
                        }
                        for grant in profile.grants
                    ],
                }
                for profile in self.profiles
            ],
        }
        payload = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def from_manifests(
        cls,
        manifests: Iterable[AnyManifest],
        profiles: Iterable[RoleGrantProfile] = (),
        roles: Iterable[RoleDefinition] = (),
    ) -> PermissionPlan:
        """Compile module definitions and product profiles without persistence."""

        catalogue = PermissionCatalogue.from_manifests(manifests)
        definitions: list[PermissionDefinition] = []
        for code in sorted(catalogue.codes()):
            spec = catalogue.require(code)
            owner = catalogue.owner(code)
            if owner is None:  # construction above makes this unreachable
                raise PermissionPlanError(f"permission {code!r} has no owning module")
            definitions.append(
                PermissionDefinition(
                    code=code,
                    description=spec.description,
                    owner=owner,
                )
            )

        normalized_roles = tuple(sorted(roles))
        role_codes: set[str] = set()
        for role in normalized_roles:
            if role.code in role_codes:
                raise PermissionPlanError(f"duplicate role definition {role.code!r}")
            role_codes.add(role.code)

        normalized_profiles = tuple(sorted(profiles, key=lambda profile: profile.code))
        profile_codes: set[str] = set()
        grant_owner: dict[RoleGrant, str] = {}
        for profile in normalized_profiles:
            if profile.code in profile_codes:
                raise PermissionPlanError(
                    f"duplicate role grant profile code {profile.code!r}"
                )
            profile_codes.add(profile.code)
            for grant in profile.grants:
                if grant.role_code not in role_codes:
                    raise PermissionPlanError(
                        f"role grant profile {profile.code!r} references undeclared "
                        f"role {grant.role_code!r}"
                    )
                if not catalogue.is_declared(grant.permission_code):
                    raise PermissionPlanError(
                        f"role grant profile {profile.code!r} references undeclared "
                        f"permission {grant.permission_code!r}"
                    )
                existing_owner = grant_owner.get(grant)
                if existing_owner is not None:
                    raise PermissionPlanError(
                        f"grant {grant!r} is owned by both profiles "
                        f"{existing_owner!r} and {profile.code!r}"
                    )
                grant_owner[grant] = profile.code

        return cls(
            definitions=tuple(definitions),
            roles=normalized_roles,
            profiles=normalized_profiles,
            grants=tuple(sorted(grant_owner)),
        )

    def is_declared(self, code: str) -> bool:
        """True when the plan contains a module-owned definition for ``code``."""

        return any(definition.code == code for definition in self.definitions)

    def diff(self, state: PermissionState) -> PermissionPlanDiff:
        """Return additive work and conflicts; never mutate or subtract state."""

        desired_codes = frozenset(item.code for item in self.definitions)
        observed_codes = state.active_permission_codes | state.inactive_permission_codes
        inactive_desired = desired_codes & state.inactive_permission_codes
        desired_roles = {role.code: role for role in self.roles}
        desired_role_codes = frozenset(desired_roles)
        observed_roles = state.active_role_codes | state.inactive_role_codes
        inactive_desired_roles = desired_role_codes & state.inactive_role_codes
        absent_desired_roles = desired_role_codes - observed_roles
        creatable_roles = frozenset(
            role_code
            for role_code in absent_desired_roles
            if desired_roles[role_code].create_if_missing
        )
        missing_roles = tuple(sorted(absent_desired_roles - creatable_roles))

        permission_inserts = tuple(
            definition
            for definition in self.definitions
            if definition.code not in observed_codes
        )
        role_inserts = tuple(desired_roles[code] for code in sorted(creatable_roles))
        grant_inserts = tuple(
            grant
            for grant in self.grants
            if grant not in state.grants
            and (
                grant.role_code in state.active_role_codes
                or grant.role_code in creatable_roles
            )
            and grant.permission_code not in inactive_desired
        )
        conflicts = tuple(
            sorted(
                (
                    *(
                        PermissionPlanConflict("inactive_permission", code)
                        for code in inactive_desired
                    ),
                    *(
                        PermissionPlanConflict("inactive_role", role_code)
                        for role_code in inactive_desired_roles
                    ),
                    *(
                        PermissionPlanConflict("missing_role", role_code)
                        for role_code in missing_roles
                    ),
                )
            )
        )
        return PermissionPlanDiff(
            permission_inserts=permission_inserts,
            role_inserts=role_inserts,
            grant_inserts=grant_inserts,
            missing_roles=missing_roles,
            conflicts=conflicts,
            preserved_permission_codes=frozenset(observed_codes - desired_codes),
            preserved_role_codes=frozenset(observed_roles - desired_role_codes),
            preserved_grants=frozenset(state.grants - frozenset(self.grants)),
        )


__all__ = [
    "PERMISSION_PLAN_SCHEMA_VERSION",
    "PermissionDefinition",
    "PermissionPlan",
    "PermissionPlanConflict",
    "PermissionPlanDiff",
    "PermissionPlanError",
    "PermissionState",
    "RoleDefinition",
    "RoleGrant",
    "RoleGrantProfile",
]
