"""Feature-flag declarations are owned, used, non-authorizing, and not stale.

The directive's flag rules, made executable. Each one exists because of a way
flags rot in real systems:

- **Flags cannot grant permissions.** The rule the whole design rests on. A flag
  says which code path runs; it never says who may run it. Enforced as disjoint
  namespaces so the two can never be confused by a reader or a guard.
- **Every declared flag has a real consumer.** A flag nobody reads is a branch
  nobody takes and a config knob that lies about having an effect.
- **Every referenced flag is declared.** Otherwise a typo is a permanent
  `UndeclaredFlagError` on a code path that looks wired.
- **Every flag has an owner.** An unowned flag is one nobody will ever delete.
- **Stale flags fail the BUILD, never production.** An expired flag keeps
  working for users; it breaks CI, which is the only place breaking it is
  useful.
"""

from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path

import pytest
from dotmac_kernel.capabilities import CapabilityCatalogue
from dotmac_kernel.flags import (
    DEPLOYMENT_SCOPE,
    FeatureFlagSpec,
    FlagCatalogue,
    FlagError,
    UndeclaredFlagError,
)
from dotmac_kernel.permissions import PermissionCatalogue

from app.assembly import assembly

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_SOURCE_ROOTS: tuple[Path, ...] = (
    PROJECT_ROOT / "packages/dotmac-kernel/src/dotmac_kernel",
    PROJECT_ROOT / "packages/dotmac-template-studio/src/dotmac_template_studio",
    PROJECT_ROOT / "app",
)


def _flags() -> FlagCatalogue:
    return FlagCatalogue.from_manifests(assembly.modules)


def _referenced_codes() -> set[str]:
    """Every string literal in the source tree that matches a declared flag
    code. Deliberately literal-based: a flag read through a computed code is a
    flag no reader can trace, and this test failing on one is the point."""
    declared = _flags().codes()
    found: set[str] = set()
    for root in _SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value in declared and "manifest" not in path.name:
                        found.add(node.value)
    return found


def test_the_assembly_declares_flags_at_all() -> None:
    """Assert on the set walked — the rules below are vacuous without one."""
    assert _flags().codes(), "no installed module declares a feature flag"


def test_flags_cannot_grant_permissions() -> None:
    """Disjoint namespaces: a code is a flag, a permission, or a capability.

    Never two of them. A code that was both would let "turn the feature on"
    silently mean "let this actor in", which is the exact conflation the
    directive forbids.
    """
    flags = _flags().codes()
    permissions = PermissionCatalogue.from_manifests(assembly.modules).codes()
    capabilities = CapabilityCatalogue.from_manifests(assembly.modules).codes()
    assert not (
        flags & permissions
    ), f"code is both flag and permission: {flags & permissions}"
    assert not (
        flags & capabilities
    ), f"code is both flag and capability: {flags & capabilities}"


def test_every_declared_flag_has_a_real_consumer() -> None:
    orphans = sorted(_flags().codes() - _referenced_codes())
    assert not orphans, (
        f"flag(s) declared but read nowhere: {orphans} — wire a "
        "`resolve_flag(...)` consumer, or drop the declaration until there is "
        "a branch to control"
    )


def test_every_flag_has_an_owner() -> None:
    """`FlagCatalogue.from_manifests` fills the owner from the declaring module,
    so this asserts the mechanism rather than the diligence of each author."""
    unowned = [spec.code for spec in _flags().specs() if not spec.owner]
    assert not unowned, f"flag(s) with no owning module: {unowned}"


def test_no_flag_is_expired() -> None:
    """A flag past its removal date fails the BUILD.

    Deliberately not a runtime check: an expiry must never take a feature down
    in production. It forces a decision here — delete the flag and its dead
    branch, or move the date on purpose.
    """
    expired = [
        f"{spec.code} (expired {spec.expires_on})" for spec in _flags().expired()
    ]
    assert not expired, (
        "expired feature flag(s) — remove the flag and the branch it guards, or "
        "move `expires_on` deliberately:\n" + "\n".join(expired)
    )


def test_the_expiry_check_would_actually_fail() -> None:
    """Sensitivity proof: the rule above must be able to fire."""
    stale = FeatureFlagSpec(
        code="probe.stale",
        owner="probe",
        expires_on=date.today() - timedelta(days=1),
    )
    assert stale.is_expired
    assert FlagCatalogue([stale]).expired() == (stale,)


def test_an_undeclared_flag_reference_raises() -> None:
    with pytest.raises(UndeclaredFlagError, match="ghost.flag"):
        _flags().require("ghost.flag")


# ── Declaration validity ────────────────────────────────────────────────────


def test_a_default_must_match_the_declared_type() -> None:
    with pytest.raises(FlagError, match="not a int"):
        FeatureFlagSpec(code="x", value_type=int, default="nope")  # type: ignore[arg-type]


def test_a_bool_default_is_not_accepted_for_an_int_flag() -> None:
    """`bool` subclasses `int`; the declared type must still mean what it says."""
    with pytest.raises(FlagError):
        FeatureFlagSpec(code="x", value_type=int, default=True)


def test_a_flag_must_allow_at_least_one_scope() -> None:
    with pytest.raises(FlagError, match="no scope"):
        FeatureFlagSpec(code="x", allowed_scopes=frozenset())


def test_an_unknown_scope_is_rejected() -> None:
    with pytest.raises(FlagError, match="unknown scope"):
        FeatureFlagSpec(
            code="x", allowed_scopes=frozenset({DEPLOYMENT_SCOPE, "galaxy"})
        )
