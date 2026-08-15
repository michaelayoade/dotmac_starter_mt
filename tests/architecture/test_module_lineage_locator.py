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

"Installable module" is DERIVED, not listed: a package declaring a
``ModuleManifest`` is installed INTO an assembly, and one that does not is the
host the assembly is built on. ``dotmac-kernel`` therefore falls out of the
second part because of what it IS. Its ``migrations`` submodule is itself a
documented public entry point — every assembly's ``alembic.ini`` composes
``dotmac_kernel.migrations.versions_dir()`` by that name, and
``dotmac-application-directory``'s own locator docstring cites it as the shape
to mirror — so a top-level alias would be a second spelling of a working import.

That reasoning was previously a named exemption set, which is a claim a reader
has to trust and which the next host package would have silently failed. A
derived predicate is a claim the code checks.

## Calling the locator, not just finding it

The static half proves a correctly-shaped function exists. That is not the
property consumers need, and on its own it is an assertion-shaped no-op: a
``versions_dir()`` returning ``/tmp`` satisfies every signature check. So the
locator is also EXECUTED — loaded from its file under a throwaway name so the
parent package's ``__init__`` never runs — and its answer compared against the
directory that really holds this distribution's revisions.

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
import importlib.util
from pathlib import Path
from types import ModuleType

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


def _is_installable_module(root: Path) -> bool:
    """Does this package declare a `ModuleManifest` — i.e. is it INSTALLED into
    an assembly, rather than being the host the assembly is built on?

    This is what decides whether the top-level re-export is owed, and it is
    DERIVED rather than listed. An exemption set naming `dotmac-kernel` was a
    claim a reader had to trust; this is one the code checks, and it keeps
    working for the next package without anyone remembering to edit a set.

    The distinction it captures: an installable module documents its top-level
    namespace as the stable surface ("Submodules are not: import from here"), so
    a locator reachable only through a submodule points its consumers at the
    half it reserves the right to move. `dotmac-kernel` ships no manifest — it
    is the host, and `dotmac_kernel.migrations` is a documented public entry
    point in its own right, composed by that exact name in every assembly's
    Alembic config. A top-level alias there would be a second spelling of an
    import that already works.

    Read statically, for the same reason as `_exports`.
    """
    manifest = root / "manifest.py"
    if not manifest.is_file():
        return False
    tree = ast.parse(manifest.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Call)
        and (
            node.func.id
            if isinstance(node.func, ast.Name)
            else getattr(node.func, "attr", "")
        )
        == "ModuleManifest"
        for node in ast.walk(tree)
    )


class AmbiguousImportRootError(AssertionError):
    """A distribution's `src/` does not contain exactly one package root."""


def _import_root(distribution: Path) -> Path:
    """The single package directory under `src/`.

    RAISES on zero or several roots rather than returning None. Returning None
    read as "not applicable" and silently removed the distribution from the
    sweep — so the guard's coverage shrank exactly where the layout was unusual,
    which is where a locator is most likely to be wrong. Ambiguity is an error
    to be fixed (here or in the package), never a quiet exclusion.

    A distribution with no `src/` at all is a different shape, not an ambiguous
    one, and is excluded before this is called.
    """
    src = distribution / "src"
    roots = [path for path in sorted(src.iterdir()) if (path / "__init__.py").is_file()]
    if len(roots) != 1:
        raise AmbiguousImportRootError(
            f"{distribution.name}: expected exactly one package root under src/, "
            f"found {[path.name for path in roots]} — this guard cannot say "
            "which one owns the lineage, and skipping the distribution would "
            "hide it from every check in this file"
        )
    return roots[0]


def _has_src_layout(distribution: Path) -> bool:
    """Is this a `src/`-layout distribution at all?

    Separate from `_import_root` so "no src/ directory" (not our shape) stays
    distinguishable from "src/ with an ambiguous number of roots" (an error).
    """
    return (distribution / "src").is_dir()


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


def _load_locator_module(locator_path: Path) -> ModuleType:
    """Execute `<pkg>/migrations/__init__.py` ALONE and return the module.

    Loaded by file path under a throwaway name, so the parent package's
    `__init__` never runs: several of these packages build a database engine at
    import time, and a guard that needed a live database to check a filesystem
    convention would be skipped exactly where it matters. The locator itself
    depends on nothing but `pathlib` and its own `__file__`, so executing it in
    isolation gives the same answer a consumer gets.
    """
    spec = importlib.util.spec_from_file_location(
        f"_lineage_locator_probe_{locator_path.parent.parent.name}", locator_path
    )
    assert spec is not None and spec.loader is not None, locator_path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _returned_path_violations(module: ModuleType, expected: Path) -> list[str]:
    """CALL the locator and check what it actually returns.

    Split out as a pure function over a loaded module so the sensitivity proof
    can drive it with a deliberately wrong locator. Static checks prove a
    callable of the right shape EXISTS; only calling it proves the path is the
    one the consumer needs — a `versions_dir()` returning `/tmp`, or the package
    root, or the parent of the right directory, satisfies every static check in
    this file and still breaks composition.
    """
    problems: list[str] = []
    locator = getattr(module, LOCATOR, None)
    if locator is None:
        return [f"module exposes no `{LOCATOR}`"]
    if not callable(locator):
        return [f"`{LOCATOR}` is not callable: {locator!r}"]

    returned = locator()
    if not isinstance(returned, Path):
        problems.append(f"`{LOCATOR}()` returned {type(returned).__name__}, not Path")
        return problems
    if not returned.is_absolute():
        problems.append(f"`{LOCATOR}()` returned a relative path: {returned}")
    if returned != expected.resolve():
        problems.append(
            f"`{LOCATOR}()` returned {returned}, but this distribution's "
            f"revisions live in {expected.resolve()}"
        )
        return problems
    if not returned.is_dir():
        problems.append(f"`{LOCATOR}()` returned {returned}, which is not a directory")
        return problems
    revisions = [
        path.name
        for path in returned.iterdir()
        if path.suffix == ".py" and path.name != "__init__.py"
    ]
    if not revisions:
        problems.append(
            f"`{LOCATOR}()` returned {returned}, which holds no revisions — the "
            "directory exists but composing it would contribute nothing"
        )
    return problems


def _is_compliant(distribution: Path) -> bool:
    """Does this distribution satisfy the rule that applies to IT?

    One function, used by both the per-module tests and the ratchet, so the two
    cannot drift into disagreeing about what compliance means — which would let
    the debt map be exact about the wrong thing.
    """
    root = _import_root(distribution)
    if not _exports(root / "migrations" / "__init__.py", LOCATOR):
        return False
    if _is_installable_module(root) and not _exports(root / "__init__.py", LOCATOR):
        return False
    return not _returned_path_violations(
        _load_locator_module(root / "migrations" / "__init__.py"),
        root / "migrations" / "versions",
    )


def _lineage_distributions() -> list[Path]:
    """Every checked-in distribution that ships Alembic revisions.

    `_import_root` RAISES on an ambiguous layout rather than skipping it, so a
    package this sweep cannot classify fails collection instead of vanishing
    from every test in this file.
    """
    found = []
    for distribution in sorted(PACKAGES_DIR.iterdir()):
        if not (distribution / "pyproject.toml").is_file():
            continue
        if not _has_src_layout(distribution):
            continue
        if _ships_a_lineage(_import_root(distribution)):
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

    assert _exports(root / "migrations" / "__init__.py", LOCATOR), (
        f"{distribution.name}: ships a lineage but {root.name}.migrations "
        f"exposes no `{LOCATOR}` — a consumer composing this module into its "
        "`version_locations` would have to reach into `__file__`"
    )
    if not _is_installable_module(root):
        # The host, not an installed module: `<pkg>.migrations` is its own
        # documented entry point. Derived from the absence of a ModuleManifest
        # rather than from a name, so it stays true for the next package.
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

    Two halves, and the second is the one that bites: the signature is read
    statically, then the function is CALLED and its answer compared against the
    directory that really holds this distribution's revisions. Checking only the
    signature proved a callable existed — a locator returning `/tmp` passed.
    """
    if distribution.name in PRE_RULE_DEBT:
        pytest.skip(f"{distribution.name}: PRE_RULE_DEBT, see the map")
    root = _import_root(distribution)

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

    # Now CALL it. Everything above describes the function; this checks what it
    # answers, which is the only thing a consumer actually uses.
    problems = _returned_path_violations(
        _load_locator_module(root / "migrations" / "__init__.py"),
        root / "migrations" / "versions",
    )
    assert not problems, f"{distribution.name}: " + "; ".join(problems)


# ── Sensitivity, in both directions ──────────────────────────────────────────


def _write_locator(directory: Path, body: str) -> Path:
    """A standalone `migrations/__init__.py` returning whatever `body` says."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "__init__.py"
    path.write_text(
        "from pathlib import Path\n\n\n"
        f"def versions_dir() -> Path:\n    return {body}\n\n\n"
        '__all__ = ["versions_dir"]\n',
        encoding="utf-8",
    )
    return path


def test_a_locator_returning_the_wrong_path_is_rejected(tmp_path: Path) -> None:
    """SENSITIVITY for the returned path — the repair this file most needed.

    The static half of the check is satisfied by ANY correctly-shaped function,
    so before this proof a `versions_dir()` returning `/tmp` passed the whole
    file. Each case below is a different way to be plausibly wrong.
    """
    package = tmp_path / "pkg"
    versions = package / "migrations" / "versions"
    versions.mkdir(parents=True)
    (versions / "xx_0001_thing.py").write_text("revision = 'x'\n", encoding="utf-8")

    correct = _write_locator(
        package / "migrations", 'Path(__file__).resolve().parent / "versions"'
    )
    assert (
        _returned_path_violations(_load_locator_module(correct), versions) == []
    ), "the positive control fails, so the negatives below prove nothing"

    wrong_dir = tmp_path / "elsewhere" / "migrations"
    cases = {
        "somewhere else entirely": ('Path("/tmp")', "revisions live in"),
        "the package root": (
            "Path(__file__).resolve().parent.parent",
            "revisions live in",
        ),
        "the parent of the right directory": (
            "Path(__file__).resolve().parent",
            "revisions live in",
        ),
        "a string, not a Path": (
            'str(Path(__file__).resolve().parent / "versions")',
            "not Path",
        ),
        "a relative path": ('Path("migrations/versions")', "revisions live in"),
    }
    for label, (body, expected) in cases.items():
        module = _load_locator_module(_write_locator(wrong_dir, body))
        problems = _returned_path_violations(module, versions)
        assert problems, f"{label} was accepted"
        assert any(expected in problem for problem in problems), (label, problems)

    # Right path, but the directory holds no revisions: composing it would
    # contribute nothing, so "the directory exists" is not enough either.
    empty_package = tmp_path / "empty"
    (empty_package / "migrations" / "versions").mkdir(parents=True)
    empty = _write_locator(
        empty_package / "migrations", 'Path(__file__).resolve().parent / "versions"'
    )
    problems = _returned_path_violations(
        _load_locator_module(empty), empty_package / "migrations" / "versions"
    )
    assert problems and "holds no revisions" in problems[0], problems


def test_an_ambiguous_or_missing_import_root_fails_rather_than_disappearing(
    tmp_path: Path,
) -> None:
    """SENSITIVITY for discovery coverage.

    `_import_root` used to return None for both shapes, and None meant "skip" —
    so a package with an unusual layout silently left the sweep. That is the
    worst possible failure for a guard: coverage shrinks exactly where the
    layout is most likely to be wrong, and nothing reports it.
    """
    two_roots = tmp_path / "dotmac-two"
    for name in ("pkg_a", "pkg_b"):
        (two_roots / "src" / name).mkdir(parents=True)
        (two_roots / "src" / name / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(AmbiguousImportRootError, match="exactly one package root"):
        _import_root(two_roots)

    no_roots = tmp_path / "dotmac-none"
    (no_roots / "src").mkdir(parents=True)
    with pytest.raises(AmbiguousImportRootError, match="exactly one package root"):
        _import_root(no_roots)

    # Specificity: the ordinary shape still resolves, or the two failures above
    # are satisfied by a function that never succeeds.
    one_root = tmp_path / "dotmac-one"
    (one_root / "src" / "pkg").mkdir(parents=True)
    (one_root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    assert _import_root(one_root) == one_root / "src" / "pkg"


def test_installable_is_derived_from_the_manifest_not_from_a_name() -> None:
    """SENSITIVITY for the scope predicate.

    The top-level re-export is owed by INSTALLABLE MODULES. That used to be an
    exemption set naming `dotmac-kernel`, which a reader had to take on trust
    and which the next host package would silently fail. Deriving it from the
    presence of a `ModuleManifest` makes it a property of the package.
    """
    assert _is_installable_module(
        PACKAGES_DIR / "dotmac-approvals/src/dotmac_approvals"
    )
    assert _is_installable_module(
        PACKAGES_DIR / "dotmac-release-catalog/src/dotmac_release_catalog"
    )
    # The host. No manifest, so it is not installed INTO an assembly; it is the
    # thing an assembly is built on.
    assert not _is_installable_module(PACKAGES_DIR / "dotmac-kernel/src/dotmac_kernel")

    # And the predicate must be reading the manifest, not the directory name.
    kernel_root = PACKAGES_DIR / "dotmac-kernel/src/dotmac_kernel"
    assert not (kernel_root / "manifest.py").is_file()


def test_the_kernel_owes_the_submodule_locator_but_not_the_re_export() -> None:
    """The derived scope, asserted against the real packages.

    Stated as its own test so the intended outcome is visible: the kernel is
    IN scope for the rule and compliant with it, rather than excused from the
    file. If it ever stopped exposing `dotmac_kernel.migrations.versions_dir()`,
    this fails.
    """
    kernel = PACKAGES_DIR / "dotmac-kernel"
    assert kernel in _LINEAGE, "the kernel ships a lineage and must be swept"
    root = _import_root(kernel)
    assert _exports(root / "migrations" / "__init__.py", LOCATOR)
    assert not _exports(root / "__init__.py", LOCATOR), (
        "the kernel has grown a top-level `versions_dir` alias — harmless, but "
        "it is a second spelling of a working import; remove it or update this "
        "test's reasoning deliberately"
    )
    assert _is_compliant(kernel)


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
        path.name
        for path in PACKAGES_DIR.iterdir()
        if (path / "pyproject.toml").is_file()
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
        "from pkg.migrations import versions_dir\n\n" '__all__ = ["versions_dir"]\n',
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

    # Every debt row must be an INSTALLABLE module. A host package cannot be in
    # debt to the top-level re-export rule, because that rule does not reach it
    # — and a row that could mean "excused" would be the ADR-0018 conflation
    # this map exists to avoid. There is no exemption list to keep disjoint
    # anymore: scope is derived, so a package is either in scope and compliant,
    # in scope and in debt, or out of scope for a checkable reason.
    for distribution in PRE_RULE_DEBT:
        assert _is_installable_module(_import_root(PACKAGES_DIR / distribution)), (
            f"{distribution} is listed as debt but declares no ModuleManifest; "
            "a host package is out of scope, not behind"
        )
