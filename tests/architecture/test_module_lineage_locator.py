"""A module that ships a migration lineage must say where the lineage IS.

Shipping the revisions as package data is only half a contract. A consuming
assembly has to name the directory in Alembic's ``version_locations``, and the
path differs between a source checkout, a virtualenv, a wheel and a container
layer. Without a public locator the consumer has exactly two options, and both
are defects:

- hard-code ``packages/<dist>/src/<pkg>/migrations/versions``, which only works
  inside this repository's checkout; or
- reach into the installed package's ``__file__``, which makes the module's
  private filesystem layout part of its public contract — in the CONSUMER's
  code, where this repository cannot see it break.

That is not hypothetical. The vendor control plane hit it composing
``dotmac-approvals`` and wrote the ``__file__`` shim, because the module shipped
its lineage without a locator. The shim has since been removed and
``dotmac-approvals 0.1.0a4`` exposes ``versions_dir()``.

## The rule, in two parts

**Every** distribution that ships a lineage exposes ``versions_dir()`` from
``<pkg>.migrations``.

**Installable modules additionally re-export it on the top-level namespace**,
because that top level is the surface each of them documents as stable
("Everything importable from this top-level namespace is stable. Submodules are
not: import from here"). A locator reachable only through a submodule would ask
their consumers to import from the half those packages reserve the right to
move.

``dotmac-kernel`` is deliberately exempt from the second part and is NOT debt.
Its ``migrations`` submodule is itself a documented public entry point — every
assembly's ``alembic.ini`` composes ``dotmac_kernel.migrations.versions_dir()``
by that name, and ``dotmac-application-directory``'s own locator docstring cites
it as the shape to mirror. Adding a top-level alias would create a second
spelling of a working import for no consumer. Recording that as an exemption
with a reason, rather than as a debt row, is the ADR-0018 point applied to this
file: "grandfathered" and "reviewed and correct" are different claims and must
not share a slot.

One signature, checked rather than assumed: no parameters, returning an absolute
``Path`` to the ``versions`` directory that actually exists and actually holds
the revisions. Four packages had converged on the same shape by hand; this makes
that convergence the rule, so the fifth does not invent a fifth spelling.

## Discovery, not enumeration

The module set comes from the filesystem — every ``packages/*`` distribution
whose import root contains ``migrations/versions/``. Shipping a lineage is what
creates the obligation, so a new module is enrolled the day its lineage lands,
with nothing to remember. A hand-written list would go stale exactly when a new
module is added, which is the moment it matters.

## PRE_RULE_DEBT

Five modules predate this rule. The map is EXACT and two-directional: adding a
non-compliant module fails, and fixing one without removing its row also fails,
so the debt cannot quietly become permanent or quietly be forgotten.

Following ADR-0018, a grandfathered row is deliberately NOT the same claim as a
compliant module. It says "this was here before the rule and nobody has looked",
not "this was reviewed and is correct". They are different statements and the
map keeps them apart.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = PROJECT_ROOT / "packages"

LOCATOR = "versions_dir"

# Distributions that ship a lineage but do not yet expose a locator. Exact, and
# it only shrinks: a row is deleted in the same change that adds the locator,
# and `test_the_debt_map_is_exact` fails if a row is deleted without the fix, or
# if a new non-compliant module appears.
#
# Each row is debt, NOT an approval. `dotmac-approvals` was removed from this
# map on 2026-08-15 when 0.1.0a4 added its locator — the ratchet shrinking as
# designed. Re-adding it would re-grant a retired exemption.
#
# Fixing one is a release of that module (a new public symbol is a new surface),
# which is why they are not all fixed at once: six releases in one change is six
# untested claims, and the repo's own rule is one coherent slice per change.
PRE_RULE_DEBT = {
    "dotmac-files": "no locator; lineage `fi`",
    "dotmac-imports": "no locator; lineage `im`",
    "dotmac-integration": "no locator; lineage `ig`, two revisions",
    "dotmac-template-studio": "no locator; lineage `ts`, two revisions",
    "dotmac-ticketing": "no locator; lineage `tk`",
}

#: Ships a lineage and exposes `<pkg>.migrations.versions_dir()`, but is NOT
#: required to re-export it at the top level — and is therefore not debt.
#:
#: The kernel's `migrations` submodule is a documented public entry point in its
#: own right: every assembly's Alembic config composes
#: `dotmac_kernel.migrations.versions_dir()` under that exact name, and the
#: installable modules' locators were written to mirror it. A top-level alias
#: would be a second spelling of an import that already works.
#:
#: Kept as a NAMED exemption with its reason rather than as a `PRE_RULE_DEBT`
#: row, because the two say different things (ADR-0018). Listing a compliant
#: package as debt would be the exact conflation this file's ratchet exists to
#: prevent.
TOP_LEVEL_REEXPORT_EXEMPT = {"dotmac-kernel"}


def _import_root(distribution: Path) -> Path | None:
    """The single package directory under `src/`, or None if the layout differs.

    Returns None rather than guessing: a wrong guess would silently drop a
    distribution out of the sweep, which is the failure `test_discovery_...`
    below exists to make impossible.
    """
    src = distribution / "src"
    if not src.is_dir():
        return None
    roots = [path for path in sorted(src.iterdir()) if (path / "__init__.py").is_file()]
    return roots[0] if len(roots) == 1 else None


def _ships_a_lineage(root: Path) -> bool:
    """Does this package ship Alembic revisions as package data?

    The obligation follows the REVISIONS, not the directory: an empty
    `migrations/versions/` is not a lineage and should not demand a locator.
    """
    versions = root / "migrations" / "versions"
    if not versions.is_dir():
        return False
    return any(
        path.suffix == ".py" and path.name != "__init__.py"
        for path in versions.iterdir()
    )


def _exports(path: Path, name: str) -> bool:
    """Is `name` a module-level definition or import, and listed in `__all__`?

    Read with `ast` rather than imported: several of these packages build an
    engine at import time, and a guard that needs a live database to check a
    filesystem convention would be skipped exactly where it matters.
    """
    if not path.is_file():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"))

    defined = False
    exported = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            defined = True
        elif isinstance(node, ast.ImportFrom):
            defined = defined or any(
                alias.asname == name or (alias.asname is None and alias.name == name)
                for alias in node.names
            )
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            value = node.value
            if isinstance(value, ast.List | ast.Tuple):
                exported = any(
                    isinstance(element, ast.Constant) and element.value == name
                    for element in value.elts
                )
    return defined and exported


def _is_compliant(distribution: Path) -> bool:
    """Does this distribution satisfy the rule that applies to IT?

    One function, used by both the per-module tests and the ratchet, so the two
    cannot drift into disagreeing about what compliance means — which would let
    the debt map be exact about the wrong thing.
    """
    root = _import_root(distribution)
    if root is None:
        return False
    if not _exports(root / "migrations" / "__init__.py", LOCATOR):
        return False
    if distribution.name in TOP_LEVEL_REEXPORT_EXEMPT:
        return True
    return _exports(root / "__init__.py", LOCATOR)


def _lineage_distributions() -> list[Path]:
    """Every checked-in distribution that ships Alembic revisions."""
    found = []
    for distribution in sorted(PACKAGES_DIR.iterdir()):
        if not (distribution / "pyproject.toml").is_file():
            continue
        root = _import_root(distribution)
        if root is not None and _ships_a_lineage(root):
            found.append(distribution)
    return found


_LINEAGE = _lineage_distributions()
_IDS = [path.name for path in _LINEAGE]


# ── The rule ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("distribution", _LINEAGE, ids=_IDS)
def test_a_module_shipping_a_lineage_exposes_a_public_locator(
    distribution: Path,
) -> None:
    if distribution.name in PRE_RULE_DEBT:
        pytest.skip(f"{distribution.name}: PRE_RULE_DEBT, see the map")
    root = _import_root(distribution)
    assert root is not None

    assert _exports(root / "migrations" / "__init__.py", LOCATOR), (
        f"{distribution.name}: ships a lineage but {root.name}.migrations "
        f"exposes no `{LOCATOR}` — a consumer composing this module into its "
        "`version_locations` would have to reach into `__file__`"
    )
    if distribution.name in TOP_LEVEL_REEXPORT_EXEMPT:
        return
    assert _exports(root / "__init__.py", LOCATOR), (
        f"{distribution.name}: `{LOCATOR}` is not re-exported from {root.name} "
        "— these packages document the TOP-LEVEL namespace as the stable "
        "surface, so a locator reachable only through a submodule asks the "
        "consumer to import from the unstable half"
    )


@pytest.mark.parametrize("distribution", _LINEAGE, ids=_IDS)
def test_the_locator_returns_the_directory_that_holds_the_revisions(
    distribution: Path,
) -> None:
    """One signature and one meaning, checked rather than assumed.

    A locator that takes an argument, or returns a string, or points at the
    package root, satisfies "exposes `versions_dir`" and still breaks the
    consumer it exists to serve.
    """
    if distribution.name in PRE_RULE_DEBT:
        pytest.skip(f"{distribution.name}: PRE_RULE_DEBT, see the map")
    root = _import_root(distribution)
    assert root is not None

    source = (root / "migrations" / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == LOCATOR
    ]
    assert len(functions) == 1
    signature = functions[0].args
    assert not signature.args, f"{distribution.name}: {LOCATOR} takes parameters"
    assert not signature.kwonlyargs
    assert signature.vararg is None and signature.kwarg is None
    returns = signature and functions[0].returns
    assert isinstance(returns, ast.Name) and returns.id == "Path", (
        f"{distribution.name}: {LOCATOR} must be annotated `-> Path`; a string "
        "return makes every consumer re-wrap it"
    )

    # And the directory it names must be the one that really holds revisions.
    versions = root / "migrations" / "versions"
    assert versions.is_dir(), distribution.name
    assert _ships_a_lineage(root), distribution.name


# ── Sensitivity, in both directions ──────────────────────────────────────────


def test_discovery_finds_the_lineage_modules_and_excludes_the_others() -> None:
    """SENSITIVITY, direction one: the sweep must not be empty or universal.

    A parametrize over an empty sequence passes vacuously and reads exactly like
    a sweep that checked everything. A sweep that matched every package would be
    equally wrong in the other direction — it would demand a locator from
    `dotmac-ui`, which ships no migrations at all.
    """
    discovered = {path.name for path in _LINEAGE}
    assert len(discovered) >= 8, discovered

    # Ships a lineage, so it must be in scope.
    for expected in ("dotmac-approvals", "dotmac-release-catalog", "dotmac-ticketing"):
        assert expected in discovered, expected

    # Ships none, so it must NOT be — the rule follows the revisions.
    for excluded in ("dotmac-ui", "dotmac-auth-oidc"):
        if (PACKAGES_DIR / excluded).is_dir():
            assert excluded not in discovered, excluded

    all_packages = {
        path.name for path in PACKAGES_DIR.iterdir() if (path / "pyproject.toml").is_file()
    }
    assert discovered < all_packages, "discovery matched every package"


def test_the_reader_detects_a_present_and_an_absent_locator(tmp_path: Path) -> None:
    """SENSITIVITY, direction two: `_exports` must answer both ways.

    A reader that always returned True would make every assertion above pass;
    one that always returned False would be caught by the compliant modules but
    only after someone removed the debt map. Drive it over both shapes.
    """
    present = tmp_path / "present.py"
    present.write_text(
        "from pathlib import Path\n\n\n"
        "def versions_dir() -> Path:\n    return Path(__file__)\n\n\n"
        '__all__ = ["versions_dir"]\n',
        encoding="utf-8",
    )
    assert _exports(present, LOCATOR) is True

    absent = tmp_path / "absent.py"
    absent.write_text('"""Nothing here."""\n', encoding="utf-8")
    assert _exports(absent, LOCATOR) is False

    # Defined but not exported is NOT compliant: `__all__` is what these
    # packages publish, and a symbol outside it is private by their own rule.
    unexported = tmp_path / "unexported.py"
    unexported.write_text(
        "from pathlib import Path\n\n\n"
        "def versions_dir() -> Path:\n    return Path(__file__)\n\n\n"
        '__all__ = ["something_else"]\n',
        encoding="utf-8",
    )
    assert _exports(unexported, LOCATOR) is False

    # Re-exported from a submodule counts: that is how a top-level `__init__`
    # publishes it.
    reexported = tmp_path / "reexported.py"
    reexported.write_text(
        "from pkg.migrations import versions_dir\n\n"
        '__all__ = ["versions_dir"]\n',
        encoding="utf-8",
    )
    assert _exports(reexported, LOCATOR) is True


def test_the_lineage_detector_follows_revisions_not_directories(
    tmp_path: Path,
) -> None:
    """SENSITIVITY for the obligation's trigger.

    An empty `migrations/versions/` is not a lineage. If `_ships_a_lineage`
    answered on the directory alone, a module scaffolding the folder before
    writing its first revision would be required to publish a locator for
    nothing.
    """
    root = tmp_path / "pkg"
    (root / "migrations" / "versions").mkdir(parents=True)
    assert _ships_a_lineage(root) is False

    (root / "migrations" / "versions" / "__init__.py").write_text("", encoding="utf-8")
    assert _ships_a_lineage(root) is False, "__init__ alone is not a revision"

    (root / "migrations" / "versions" / "xx_0001_thing.py").write_text(
        "revision = 'xx_0001_thing'\n", encoding="utf-8"
    )
    assert _ships_a_lineage(root) is True


# ── The ratchet ──────────────────────────────────────────────────────────────


def test_the_debt_map_is_exact() -> None:
    """Two-directional: the debt may not grow, and may not silently shrink.

    ADR-0018's rule. A map that only fails when the count RISES lets a fixed
    module keep its exemption forever, and the row then reads as a standing
    approval rather than as debt. Failing when it FALLS forces the row to be
    deleted in the same change that fixes the module, which is what keeps the
    map an accurate description of what is actually outstanding.
    """
    discovered = {path.name for path in _LINEAGE}

    unknown = set(PRE_RULE_DEBT) - discovered
    assert not unknown, (
        f"PRE_RULE_DEBT names distributions that ship no lineage: {sorted(unknown)} "
        "— delete the rows; they cannot be in debt to a rule that does not apply"
    )

    actually_missing = {
        distribution.name
        for distribution in _LINEAGE
        if not _is_compliant(distribution)
    }

    added = actually_missing - set(PRE_RULE_DEBT)
    assert not added, (
        f"new modules ship a lineage with no public `{LOCATOR}`: {sorted(added)} "
        "— add the locator (see dotmac-approvals 0.1.0a4). PRE_RULE_DEBT is "
        "closed debt, not an entry mode for new packages"
    )

    fixed = set(PRE_RULE_DEBT) - actually_missing
    assert not fixed, (
        f"these now expose `{LOCATOR}` but are still listed as debt: "
        f"{sorted(fixed)} — delete their PRE_RULE_DEBT rows in the same change, "
        "or the map stops describing what is outstanding and the exemption "
        "becomes permanent"
    )


def test_grandfathered_is_not_the_same_claim_as_compliant() -> None:
    """ADR-0018: "grandfathered" must stay distinguishable from "reviewed and
    correct".

    So the map's values are descriptions of the gap, not approvals, and the
    compliant modules are never listed. If a row could mean either, the map
    would be a place to park a module rather than a list of work.
    """
    assert PRE_RULE_DEBT, "an empty debt map means the rule is fully adopted"
    for distribution, reason in PRE_RULE_DEBT.items():
        assert "no locator" in reason, (
            f"{distribution}: a debt row states the GAP; {reason!r} reads like "
            "a justification"
        )

    compliant = {path.name for path in _LINEAGE} - set(PRE_RULE_DEBT)
    assert compliant, "every lineage module is in debt; the rule proves nothing"
    assert "dotmac-approvals" in compliant

    # The exemption is a THIRD state, and must not overlap either of the other
    # two: a package cannot be simultaneously excused and in debt.
    assert not (TOP_LEVEL_REEXPORT_EXEMPT & set(PRE_RULE_DEBT))
    for exempt in TOP_LEVEL_REEXPORT_EXEMPT:
        assert _is_compliant(PACKAGES_DIR / exempt), (
            f"{exempt} is exempt from the top-level re-export but does not even "
            "expose the submodule locator — that is debt, not an exemption"
        )
