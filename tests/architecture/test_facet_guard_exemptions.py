"""The route-guard exemptions must state premises a machine can check.

`tests/architecture/test_route_guards.py` is the guard.  This file is the guard
ON that guard, and it exists because every part of a route-guard check can go
quietly wrong without failing:

* `AUTH_GUARD_NAMES` is a set of STRINGS compared against dependency callable
  names.  A rename or a typo removes a guard from the accepted tier and nothing
  fails — the check simply stops recognising it, and every route that relied on
  it starts passing for the wrong reason.
* `ALLOWLIST` and `MUTATING_ALLOWLIST` are `(method, path)` pairs.  A route that
  is renamed or deleted leaves its exemption behind, and that stale pair is now
  a standing permission for whatever future route claims the same path.
* Each exemption's justification is a source COMMENT.  ADR-0018 § 2 is explicit
  that a premise nobody can check is not an exemption; it is an unmonitored
  region.

So this file replaces prose justification with a derivation.  Every mutating
exemption in the reference assembly is one of exactly two things, and both are
machine-checkable:

1. **Not a composed browser route at all** — a JSON API route, whose name does
   not carry the `web:` qualification `mount_web_surfaces` applies.  The facet
   CSRF/admission contract does not reach it, and it is guarded by the API's own
   tier rules.
2. **An assembly-declared ENTRY route** — `WebFacetMount.entry_routes` is the
   assembly saying "this route is reachable before admission", which is exactly
   the "you cannot require a login to log in" premise, stated as data in
   `app/assembly.py` rather than as a comment in a test.

That second form is the interesting one: it means an exemption cannot be added
by editing the test.  It has to be declared by the assembly, where the facet's
authentication profile and admission permission sit next to it and a reviewer
sees all three together.
"""

from __future__ import annotations

import importlib
from typing import Final

from dotmac_kernel.web_surfaces import qualified_route_name
from fastapi.routing import APIRoute

from app.assembly import assembly
from app.main import app
from tests.architecture.test_route_guards import (
    ALLOWLIST,
    AUTH_GUARD_NAMES,
    MUTATING_ALLOWLIST,
)

#: Where each accepted auth-tier guard is defined.  Listed explicitly rather
#: than searched, so moving a guard between kernel modules is a decision that
#: shows up here instead of a lookup that silently finds it somewhere else.
_GUARD_HOMES: Final[tuple[str, ...]] = (
    "dotmac_kernel.deps",
    "dotmac_kernel.web_deps",
    "dotmac_kernel.platform_auth",
    "dotmac_kernel.platform_web",
)

#: Guards that prove CONTEXT, never an authenticated actor.  `require_tenant`
#: says "this request resolved to a known tenant"; `require_platform_host` says
#: "this request arrived on the platform root domain".  Both start with
#: `require_`, which is why `test_every_route_has_a_guard` accepts them and why
#: the tiered check must not.  Pinned here so a future edit that "tidies" them
#: into the accepted set fails loudly.
_CONTEXT_ONLY_GUARDS: Final[frozenset[str]] = frozenset(
    {"require_tenant", "require_platform_host"}
)

_MUTATING: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Frozen exemption counts (ADR-0018 § 3).  Two-directional: adding an
#: exemption fails, and REMOVING one fails until the number is lowered in the
#: same change, so retirement is recorded rather than absorbed.
_FROZEN_EXEMPTIONS: Final[dict[str, int]] = {
    "ALLOWLIST": 3,
    "MUTATING_ALLOWLIST": 6,
}


def _routes() -> dict[tuple[str, str], APIRoute]:
    found: dict[tuple[str, str], APIRoute] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            found[(method, route.path)] = route
    return found


def _is_composed_browser_route(route: APIRoute) -> bool:
    """Composed browser routes carry the qualified name the runtime applies."""
    return route.name.startswith("web:")


def declared_entry_route_names() -> frozenset[str]:
    """Every qualified route name the ASSEMBLY declared as a facet entry.

    This is the data behind the "genuinely pre-auth" premise: the assembly
    lists a facet's entry routes beside that facet's authentication profile
    and admission permission, so the exemption and the thing it exempts you
    from are declared in one place.
    """
    return frozenset(
        qualified_route_name(facet.code, ref.module, ref.surface, ref.route_name)
        for facet in assembly.web_facets
        for ref in facet.entry_routes
    )


def exemption_premise(route: APIRoute) -> str | None:
    """The machine-checked reason this route may skip the auth-tier guard.

    Returns the premise's name, or ``None`` when no premise applies — which is
    what makes an unjustifiable exemption detectable rather than merely
    undocumented.
    """
    if not _is_composed_browser_route(route):
        return "not-a-composed-browser-route"
    if route.name in declared_entry_route_names():
        return "assembly-declared-facet-entry-route"
    return None


# ---------------------------------------------------------------------------
# The accepted guard names must name real guards.
# ---------------------------------------------------------------------------


def test_every_named_auth_tier_guard_resolves_to_a_real_kernel_callable() -> None:
    """A string that names nothing silently shrinks the accepted tier.

    `AUTH_GUARD_NAMES` is compared against `__name__` values collected from the
    dependency tree. If a guard is renamed and the set is not updated, the
    tiered check keeps passing — it just stops recognising that guard, and any
    route defended only by it becomes accepted for the wrong reason.
    """
    modules = [importlib.import_module(name) for name in _GUARD_HOMES]
    unresolved = [
        name
        for name in sorted(AUTH_GUARD_NAMES)
        if not any(callable(getattr(module, name, None)) for module in modules)
    ]
    assert not unresolved, (
        "AUTH_GUARD_NAMES names callable(s) that no kernel module defines — "
        "a renamed or deleted guard leaves the tiered check silently weaker: "
        + ", ".join(unresolved)
    )


def test_context_guards_are_deliberately_outside_the_auth_tier() -> None:
    """`require_tenant` resolving a tenant is not an authenticated actor.

    This is the exact gap the tiered check was written to close (a mutation
    reachable with only a resolved tenant). Pin it, so the set cannot be
    "simplified" back to a `require_` prefix match.
    """
    overlap = _CONTEXT_ONLY_GUARDS & AUTH_GUARD_NAMES
    assert not overlap, (
        "context-only guard(s) were added to the authentication tier — these "
        "prove a tenant or a host, never an actor: " + ", ".join(sorted(overlap))
    )


# ---------------------------------------------------------------------------
# The exemptions must name routes that exist, for premises that hold.
# ---------------------------------------------------------------------------


def test_every_guard_exemption_names_a_route_that_exists() -> None:
    """A stale exemption is a standing permission for a future route.

    `("POST", "/admin/login")` means "this specific route is pre-auth". If the
    route moves and the pair stays, the pair now exempts whatever next claims
    that path — silently, and with the original comment still above it
    explaining a route that is gone.
    """
    routes = _routes()
    assert routes, "no routes composed; every assertion here is vacuous"
    stale = [
        f"{name}: {method} {path}"
        for name, entries in (
            ("ALLOWLIST", ALLOWLIST),
            ("MUTATING_ALLOWLIST", MUTATING_ALLOWLIST),
        )
        for method, path in sorted(entries)
        if (method, path) not in routes
    ]
    assert not stale, (
        "guard exemption(s) name routes this assembly does not mount — remove "
        "the entry in the same change that removed the route:\n" + "\n".join(stale)
    )


def test_every_mutating_exemption_states_a_machine_checked_premise() -> None:
    """ADR-0018 § 2, applied to the one allowlist that skips authentication.

    Every entry must be either a non-browser route (outside the facet
    contract) or a route the ASSEMBLY declared as a facet entry. A browser
    mutation that is neither has no premise at all — only a comment.
    """
    routes = _routes()
    unjustified: list[str] = []
    for method, path in sorted(MUTATING_ALLOWLIST):
        route = routes.get((method, path))
        if route is None:
            continue  # reported by the staleness test above
        if exemption_premise(route) is None:
            unjustified.append(f"{method} {path} (route name {route.name})")
    assert not unjustified, (
        "mutating exemption(s) with no machine-checkable premise: these are "
        "composed browser routes that the assembly never declared as facet "
        "entry routes. Declare them in `WebFacetMount.entry_routes` beside the "
        "facet's authentication profile, or give them an auth-tier guard:\n"
        + "\n".join(unjustified)
    )


def test_the_exemption_premise_check_has_a_sensitivity_proof() -> None:
    """Every real exemption satisfies a premise, so prove one that shouldn't.

    Without this, `exemption_premise` returning a truthy value for everything
    would look identical to a correct check.
    """
    entries = declared_entry_route_names()
    assert entries, "the assembly declares no facet entry routes; premise is vacuous"

    class _Fake:
        def __init__(self, name: str) -> None:
            self.name = name

    real_entry = next(iter(sorted(entries)))
    assert exemption_premise(_Fake(real_entry)) == (  # type: ignore[arg-type]
        "assembly-declared-facet-entry-route"
    )
    assert exemption_premise(_Fake("login")) == (  # type: ignore[arg-type]
        "not-a-composed-browser-route"
    )
    # A composed browser mutation the assembly never declared as an entry — the
    # shape this whole file exists to refuse.
    assert exemption_premise(_Fake("web:staff_admin:parties:legacy:delete")) is None  # type: ignore[arg-type]


def test_the_exemption_sets_are_a_two_directional_ratchet() -> None:
    """Fails when an exemption is ADDED and when one is retired unrecorded."""
    live = {"ALLOWLIST": len(ALLOWLIST), "MUTATING_ALLOWLIST": len(MUTATING_ALLOWLIST)}
    assert live == _FROZEN_EXEMPTIONS, (
        "the guard exemption counts drifted.\n"
        f"  frozen: {_FROZEN_EXEMPTIONS}\n  live:   {live}\n"
        "Adding an exemption needs a machine-checkable premise (see this "
        "file's docstring). RETIRING one is progress — lower the frozen count "
        "in the same change so the reduction is recorded rather than absorbed."
    )


# ---------------------------------------------------------------------------
# Facet-wide coverage: no browser mutation outside a declared facet.
# ---------------------------------------------------------------------------


def test_every_declared_facet_composes_at_least_one_route() -> None:
    """A facet with no routes is a shell, a prefix and a nav pointing nowhere.

    It also makes every per-facet assertion elsewhere pass vacuously for that
    facet, which is worse than the empty facet itself.
    """
    facets = tuple(assembly.web_facets)
    assert facets, "the reference assembly declares no facets"
    mounted = {route.name for route in app.routes if isinstance(route, APIRoute)}
    empty = [
        facet.code
        for facet in facets
        if not any(name.startswith(f"web:{facet.code}:") for name in mounted)
    ]
    assert not empty, "declared facet(s) compose no routes at all: " + ", ".join(empty)


def test_every_composed_browser_mutation_belongs_to_a_declared_facet() -> None:
    """A `web:`-qualified route names its facet; that facet must be declared.

    The qualified name is generated from the facet the registry validated, so
    this cannot fail today. It is pinned because the CSRF, admission and
    navigation contracts all key off the facet segment — a browser mutation
    attributed to an undeclared facet would be outside all three.
    """
    declared = {facet.code for facet in assembly.web_facets}
    mutations: list[str] = []
    orphans: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not _is_composed_browser_route(route):
            continue
        if not _MUTATING.intersection(route.methods or set()):
            continue
        mutations.append(route.name)
        if route.name.split(":")[1] not in declared:
            orphans.append(route.name)
    assert mutations, (
        "no composed browser mutations found; the facet security contract is "
        "being asserted over nothing"
    )
    assert not orphans, "composed mutation(s) name an undeclared facet: " + ", ".join(
        orphans
    )
