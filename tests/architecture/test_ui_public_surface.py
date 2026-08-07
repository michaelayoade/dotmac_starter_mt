"""The `dotmac-ui` consumer boundary (ADR-0006 D5) — enforced, not documented.

D5 admits `dotmac_ui` into the product architecture allowlists through a
**narrow, explicit** boundary: only the released top-level public surface, only
alongside a real consumer, and only with the guard that keeps it narrow moving
in the same change. This file is that guard. It is the deliberate analogue of
`test_kernel_public_surface.py` — same AST approach, same "the package's own
`__init__` is the single canonical manifest" rule — plus one check the kernel
does not need.

Three things are asserted:

1. **The assembly imports only the published UI surface.** Same shape as the
   kernel check: a name must be in `dotmac_ui.__all__`, or in the `__all__` of a
   module listed in `SUPPORTED_MODULES`. Reaching into `dotmac_ui.build` (the
   asset generator) or an undeclared name fails here.
2. **The UI package imports nothing it must not.** This is the one the kernel
   analogue has no equivalent of, and it is the load-bearing one. ADR-0006 § 2
   says `dotmac-ui` "never imports a module or reads a database. It renders what
   it is given." A design system that has quietly acquired a SQLAlchemy import
   is no longer a design system, and by the time anyone notices, three products
   depend on it. The check is deliberately stricter than the ADR requires — no
   kernel either — because that is what makes the package consumable by
   `dotmac_erp`, which has adopted no kernel at all (`docs/inventories/
   erp-vendor-surfaces.md`).
3. **No token is named by value.** `--teal`, `--gold` (ERP's 77 value-named
   tokens) is how a token system stops being able to change: the name outlives
   the colour, and the day the brand goes green the token is a lie. Role naming
   is the entire point of the vocabulary, and a rule that is only in a docstring
   is a rule that erodes.

AST-based, not string matching: a `dotmac_ui` mention in a comment or a string
never trips it.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import dotmac_ui

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP_ROOT = REPO_ROOT / "app"
UI_ROOT = Path(dotmac_ui.__file__).resolve().parent

# Top-level packages `dotmac_ui` may never import. Each is a capability the
# design system is defined by NOT having:
#   app / dotmac_kernel   — ADR-0006 § 2's one-way dependency (and the ERP
#                           adoption path, see this module's docstring).
#   sqlalchemy / psycopg / alembic — "reads no database". A token package that
#                           can open a session will eventually resolve a brand
#                           itself, which is the kernel's job.
#   fastapi / starlette   — mounts no route. Presentation, not a surface.
#   jinja2                — no templating engine at 0.1.0a1. The Jinja/HTMX
#                           component API ADR-0006 § 2 anticipates is a LATER
#                           slice; until it lands with its own contract, an
#                           import here would be scope creep nobody voted for.
#   pydantic              — declares no schema; tokens are frozen dataclasses.
FORBIDDEN_IMPORTS = frozenset(
    {
        "alembic",
        "app",
        "dotmac_kernel",
        "fastapi",
        "jinja2",
        "psycopg",
        "pydantic",
        "sqlalchemy",
        "starlette",
    }
)

# Substrings that name a COLOUR or a VALUE rather than a ROLE. Drawn from the
# two value-named vocabularies the inventories found in the fleet: ERP's live
# tokens (`--teal`, `--gold`, `--ink`, `--parchment`) and Sub's dead
# `src/css/base/_tokens.css`. Ramp step digits (50…950) are positional, not
# descriptive, and are not matched here.
VALUE_WORDS = frozenset(
    {
        "amber",
        "black",
        "blue",
        "cyan",
        "emerald",
        "fuchsia",
        "gold",
        "gray",
        "green",
        "grey",
        "indigo",
        "ink",
        "lime",
        "orange",
        "parchment",
        "pink",
        "purple",
        "red",
        "rose",
        "sky",
        "slate",
        "teal",
        "violet",
        "white",
        "yellow",
    }
)


def _public_names(module: str) -> set[str]:
    """The declared public names of a supported UI module (its __all__)."""
    mod = importlib.import_module(module)
    return set(getattr(mod, "__all__", ()))


def _iter_py_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def find_surface_violations(rel_path: str, source: str) -> list[str]:
    """Return 'rel:line reason' for every import of an undocumented UI name or
    an unsupported/internal UI module."""
    violations: list[str] = []
    top_level = set(dotmac_ui.__all__)
    supported = dotmac_ui.SUPPORTED_MODULES
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "dotmac_ui":
                for alias in node.names:
                    if alias.name not in top_level:
                        violations.append(
                            f"{rel_path}:{node.lineno} `from dotmac_ui import "
                            f"{alias.name}` — not in the curated top-level __all__"
                        )
            elif mod.startswith("dotmac_ui."):
                if mod not in supported:
                    violations.append(
                        f"{rel_path}:{node.lineno} imports from `{mod}` — not a "
                        "SUPPORTED_MODULE (internal/undocumented UI module)"
                    )
                    continue
                allowed = _public_names(mod)
                for alias in node.names:
                    if alias.name not in allowed:
                        violations.append(
                            f"{rel_path}:{node.lineno} `from {mod} import "
                            f"{alias.name}` — not in {mod}.__all__"
                        )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == "dotmac_ui":
                    continue
                if name.startswith("dotmac_ui.") and name not in supported:
                    violations.append(
                        f"{rel_path}:{node.lineno} `import {name}` — not a "
                        "SUPPORTED_MODULE"
                    )
    return violations


def find_forbidden_imports(rel_path: str, source: str) -> list[str]:
    """Return 'rel:line reason' for every import of a package the UI package
    must not depend on."""
    violations: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        roots: list[tuple[str, int]] = []
        if isinstance(node, ast.ImportFrom):
            # `from . import x` has module None / level > 0 — relative, fine.
            if node.level == 0 and node.module:
                roots.append((node.module.split(".")[0], node.lineno))
        elif isinstance(node, ast.Import):
            roots.extend(
                (alias.name.split(".")[0], node.lineno) for alias in node.names
            )
        for root, lineno in roots:
            if root in FORBIDDEN_IMPORTS:
                violations.append(
                    f"{rel_path}:{lineno} imports `{root}` — dotmac_ui has no "
                    "business logic, reads no database, mounts no route, and "
                    "depends on no kernel or module (ADR-0006 § 2)"
                )
    return violations


def test_assembly_imports_only_public_ui_surface() -> None:
    violations: list[str] = []
    for path in _iter_py_files(APP_ROOT):
        rel = "app/" + str(path.relative_to(APP_ROOT)).replace("\\", "/")
        violations.extend(find_surface_violations(rel, path.read_text()))
    assert not violations, (
        "Assembly imports of undocumented dotmac_ui names/modules "
        "(add to the module's __all__ + COMPATIBILITY.md if intentionally "
        "public, or stop importing a UI internal):\n" + "\n".join(violations)
    )


def test_ui_package_imports_nothing_it_must_not() -> None:
    violations: list[str] = []
    for path in _iter_py_files(UI_ROOT):
        rel = "dotmac_ui/" + str(path.relative_to(UI_ROOT)).replace("\\", "/")
        violations.extend(find_forbidden_imports(rel, path.read_text()))
    assert not violations, "dotmac_ui reached outside its boundary:\n" + "\n".join(
        violations
    )


def test_ui_package_declares_no_runtime_dependencies() -> None:
    """The zero-dependency claim is in `pyproject.toml`, so check it there.

    `dotmac_ui` being importable from a bare interpreter is what makes it
    adoptable by a product that shares none of this repo's stack. A dependency
    added here would not fail any import test — it would just quietly become an
    adoption prerequisite for every consumer.
    """
    pyproject = (REPO_ROOT / "packages/dotmac-ui/pyproject.toml").read_text()
    block = pyproject.split("[tool.poetry.dependencies]", 1)[1].split("[", 1)[0]
    declared = [
        line.split("=", 1)[0].strip()
        for line in block.splitlines()
        if "=" in line and not line.strip().startswith("#")
    ]
    assert declared == ["python"], (
        "dotmac-ui declares runtime dependencies beyond python: "
        f"{declared}. See ADR-0006 D3/D5 and this module's docstring — if a "
        "dependency is genuinely required, that is a decision to record, not a "
        "line to add."
    )


def test_no_token_is_named_by_value() -> None:
    offenders: list[str] = []
    for name in dotmac_ui.token_names():
        parts = set(name.split("-"))
        for word in sorted(parts & VALUE_WORDS):
            offenders.append(f"{name} (contains value word {word!r})")
    assert not offenders, (
        "Design tokens must be named by ROLE, not by value — a token called "
        "`--dmui-teal` cannot survive the brand changing:\n" + "\n".join(offenders)
    )


def test_manifest_modules_are_importable_and_declare_all() -> None:
    """Every SUPPORTED_MODULE imports and declares an __all__; INTERNAL and
    SUPPORTED sets are disjoint."""
    assert not (dotmac_ui.SUPPORTED_MODULES & dotmac_ui.INTERNAL_MODULES)
    for module in sorted(dotmac_ui.SUPPORTED_MODULES):
        mod = importlib.import_module(module)
        assert hasattr(mod, "__all__"), f"{module} declares no __all__"
        assert mod.__all__, f"{module}.__all__ is empty"


def test_no_component_class_is_published_without_its_contract() -> None:
    """U1 ships the token foundation, NOT the component library.

    ADR-0006 § 5 forbids extracting a component because two implementations
    look similar — it needs two consumers of the same CONTRACT, a named owner,
    and a migration path. The reserved class namespace exists so a later slice
    has somewhere to put one; this test makes "we published a class" a
    deliberate act (add it here and to COMPATIBILITY.md) rather than something
    that happens because a stylesheet grew a selector.
    """
    stylesheet = dotmac_ui.stylesheet_path().read_text(encoding="utf-8")
    published = dotmac_ui.PUBLISHED_COMPONENT_CLASSES
    leaked = [
        cls
        for cls in _selector_classes(stylesheet)
        if cls.startswith(dotmac_ui.CLASS_PREFIX) and cls not in published
    ]
    assert not leaked, (
        "The compiled stylesheet defines component classes that are not in "
        f"PUBLISHED_COMPONENT_CLASSES: {sorted(set(leaked))}"
    )


def _selector_classes(css: str) -> list[str]:
    import re

    return re.findall(r"\.([a-zA-Z_][\w-]*)", css)


def test_checker_flags_private_and_undeclared_imports() -> None:
    """Sensitivity self-test — both checkers must bite, and must not
    false-positive on a legitimate import."""
    assert find_surface_violations(
        "app/_probe_.py", "from dotmac_ui.build import render_stylesheet\n"
    ), "checker missed an import from an INTERNAL module"
    assert find_surface_violations(
        "app/_probe_.py", "from dotmac_ui.tokens import _build_tokens\n"
    ), "checker missed a name absent from a supported module's __all__"
    assert find_surface_violations(
        "app/_probe_.py", "from dotmac_ui import _private\n"
    ), "checker missed a private top-level name"
    assert (
        find_surface_violations(
            "app/_probe_.py",
            "import dotmac_ui\n"
            "from dotmac_ui import static_dir, stylesheet_url\n"
            "from dotmac_ui.tokens import resolve_color\n"
            "# dotmac_ui.build in a comment must not trip the checker\n",
        )
        == []
    )

    assert find_forbidden_imports(
        "dotmac_ui/_probe_.py", "from sqlalchemy import select\n"
    ), "forbidden-import checker missed an ORM import"
    assert find_forbidden_imports(
        "dotmac_ui/_probe_.py", "import dotmac_kernel.templating\n"
    ), "forbidden-import checker missed a kernel import"
    assert (
        find_forbidden_imports(
            "dotmac_ui/_probe_.py",
            "from dotmac_ui.tokens import TOKENS\nfrom pathlib import Path\n",
        )
        == []
    )
