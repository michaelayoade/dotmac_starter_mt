"""Role vocabulary: code roles, declared-only composition, SoD pair warnings.

Pure over the process-active permission catalogue; no I/O.

**Code roles.** Today authorization is `PermissionSpec.default_roles`: a party
satisfies a permission by holding a role whose slug that spec lists. So the roles
an assembly "declares in code" are exactly the slugs that appear in some
`default_roles`. They are shown read-only here, and a tenant role may never take
one of those slugs (it would silently inherit that code role's permissions).

**Composition.** A role's permissions may only be chosen from declared
`PermissionSpec`s. `validate_composition` is the single place that refuses an
undeclared code.
TODO(programme section 1.2): there is no kernel role-to-permission store or
evaluator that honours a runtime composition, so a composition cannot be
persisted yet. This module does not invent one.

**SoD.** An assembly registers role pairs one person should not hold together.
Violations are WARNINGS; enforcement is the tenant dual-control policy (default
off, never compulsory).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from dotmac_kernel.exceptions import BadRequestError
from dotmac_kernel.permissions import PermissionCatalogue, active_permissions


@dataclass(frozen=True, slots=True)
class CodeRole:
    slug: str
    permission_codes: tuple[str, ...]
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class SodWarning:
    roles: tuple[str, str]
    message: str


def code_roles(catalogue: PermissionCatalogue | None = None) -> tuple[CodeRole, ...]:
    catalogue = active_permissions() if catalogue is None else catalogue
    by_slug: dict[str, set[str]] = {}
    for code in catalogue.codes():
        spec = catalogue.spec(code)
        if spec is None:  # pragma: no cover - codes() and spec() agree
            continue
        for slug in spec.default_roles:
            by_slug.setdefault(slug, set()).add(code)
    return tuple(
        CodeRole(slug=slug, permission_codes=tuple(sorted(codes)))
        for slug, codes in sorted(by_slug.items())
    )


def code_role_slugs(catalogue: PermissionCatalogue | None = None) -> frozenset[str]:
    return frozenset(role.slug for role in code_roles(catalogue))


def validate_composition(
    permission_codes: Iterable[str], catalogue: PermissionCatalogue | None = None
) -> tuple[str, ...]:
    """The sorted, de-duplicated codes, or `BadRequestError` naming every
    undeclared one. Fails closed: an empty catalogue declares nothing."""
    catalogue = active_permissions() if catalogue is None else catalogue
    wanted = sorted(set(permission_codes))
    undeclared = [code for code in wanted if not catalogue.is_declared(code)]
    if undeclared:
        raise BadRequestError(
            "undeclared permission code(s): " + ", ".join(sorted(undeclared))
        )
    return tuple(wanted)


_SOD_PAIRS: set[frozenset[str]] = set()


def register_sod_pairs(pairs: Iterable[tuple[str, str]]) -> None:
    """Register role pairs that must not be held together (assembly-owned).

    Import-time registration, the same shape as Template Studio's
    `register_contexts`. A pair of one role with itself is refused.
    """
    for first, second in pairs:
        if first == second or not first or not second:
            raise ValueError("an SoD pair needs two different, non-empty role slugs")
        _SOD_PAIRS.add(frozenset((first, second)))


def registered_sod_pairs() -> frozenset[frozenset[str]]:
    return frozenset(_SOD_PAIRS)


def sod_warnings(role_slugs: Iterable[str]) -> tuple[SodWarning, ...]:
    held = set(role_slugs)
    found: list[SodWarning] = []
    for pair in sorted(_SOD_PAIRS, key=sorted):
        if pair <= held:
            first, second = sorted(pair)
            found.append(
                SodWarning(
                    roles=(first, second),
                    message=(
                        f"roles {first!r} and {second!r} are a separation-of-duties "
                        "pair; the tenant dual-control policy decides enforcement"
                    ),
                )
            )
    return tuple(found)


__all__ = [
    "CodeRole",
    "SodWarning",
    "code_role_slugs",
    "code_roles",
    "register_sod_pairs",
    "registered_sod_pairs",
    "sod_warnings",
    "validate_composition",
]
