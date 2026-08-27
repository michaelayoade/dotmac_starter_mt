"""What `universal-facility` has to MEAN for `dotmac-deployment-foundation`.

`tests/architecture/test_product_first_extraction.py` already governs the
`stateless-protocol-adapter` word with `stateless_adapter_violations`. This file
does the same job for the facility ADR-0070 creates, and it is written to the
same shape on purpose: a pure function over a DIRECTORY, so it governs whatever
package next claims the word rather than this one specifically, and so the
sensitivity proof can build a synthetic package and watch the checker fire.

`universal-facility` currently has one other member, `dotmac-kernel`, which this
checker deliberately does NOT govern. The kernel is a facility in the sense of
being non-optional and lineage-free, but it is imported by a running
application and therefore legitimately depends on SQLAlchemy and a web
framework. The properties below are the DEPLOYMENT facility's, keyed on the
package rather than on the classification, and that narrowness is stated rather
than hidden: a checker that claimed to govern every `universal-facility` and
then exempted the only other one would be an exemption pretending to be a rule
(ADR-0018).

## The five properties, and the failure each prevents

1. **No lineage, no manifest, no migrations.** A facility that decides how a
   deployment is built cannot be a table inside one of the deployments it
   builds.
2. **No persistence, web-framework or templating import.** A build runner
   rendering a Compose file has no database and no web framework, and must not
   acquire them to validate a descriptor.
3. **No kernel import.** The same boundary from the other side, and the reason
   this is a distinct check from (2): importing `dotmac_kernel` would pull
   SQLAlchemy in transitively while the facility's own import list stayed
   clean.
4. **Zero declared runtime dependencies.** The property (2) and (3) are trying
   to protect, stated where a consumer's resolver can see it. `dotmac-ui` holds
   the same line for the same reason.
5. **No product or provider branch.** ADR-0024 § 4 and ADR-0070 § 3: every
   difference between ERP, Sub, Integrator and Starter is a value in the typed
   descriptor, never an `if` in shared code.

Every check is an AST walk, never a substring scan. The Workspace `.dmui-*`
guard once failed CI on a class name that appeared only inside a comment
explaining why inventing it was wrong; a guard that reads its own documentation
gets disabled, and a disabled guard protects nothing.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = PROJECT_ROOT / "packages" / "dotmac-deployment-foundation"

# Roots that mean "this holds rows after all", or "this is a runtime".
FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "sqlalchemy",
        "alembic",
        "psycopg",
        "psycopg2",
        "asyncpg",
        "fastapi",
        "starlette",
        "jinja2",
        "pydantic",
        "yaml",
        "requests",
        "httpx",
        "dotmac_kernel",
        "dotmac_ui",
        "app",
    }
)

_LINEAGE_DECLARATIONS = frozenset({"short_code", "migration_prefix"})

# Product identity a shared execution path may not compare against.
_PRODUCT_IDENTITIES = frozenset(
    {
        "erp",
        "sub",
        "integrator",
        "starter",
        "dotmac_erp",
        "dotmac_sub",
        "dotmac_integrator",
    }
)


def _python_files(package_dir: Path) -> list[Path]:
    return sorted((package_dir / "src").rglob("*.py"))


def _import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # A relative import has no module root to police; `level > 0` means
            # it is inside this package by construction.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".", 1)[0])
    return roots


def facility_violations(package_dir: Path, *, label: str = "") -> list[str]:
    """Every way ``package_dir`` fails to be a stateless deployment facility.

    A pure function over a directory so a test can build a synthetic package and
    prove the checker bites. Returns problems rather than raising, so a real
    package reports all of them at once.
    """
    name = label or package_dir.name
    problems: list[str] = []

    for forbidden in ("migrations", "alembic", "versions"):
        for found in package_dir.rglob(forbidden):
            if found.is_dir():
                problems.append(
                    f"{name}: contains a {forbidden}/ directory at "
                    f"{found.relative_to(package_dir)} — a facility owns no lineage"
                )

    for path in _python_files(package_dir):
        relative = path.relative_to(package_dir)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (
            SyntaxError
        ) as exc:  # pragma: no cover - a syntax error fails elsewhere too
            problems.append(f"{name}: {relative} does not parse ({exc})")
            continue

        forbidden = sorted(_import_roots(tree) & FORBIDDEN_IMPORT_ROOTS)
        if forbidden:
            problems.append(
                f"{name}: {relative} imports {forbidden}. The facility runs on a "
                "build runner with no database and no web framework"
            )

        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "ModuleManifest":
                problems.append(
                    f"{name}: {relative}:{node.lineno} references ModuleManifest — "
                    "a facility is not an installable module"
                )
            if isinstance(node, ast.Attribute) and node.attr == "ModuleManifest":
                problems.append(
                    f"{name}: {relative}:{node.lineno} references ModuleManifest"
                )
            if isinstance(node, ast.keyword) and node.arg in _LINEAGE_DECLARATIONS:
                problems.append(
                    f"{name}: {relative}:{node.value.lineno} declares "
                    f"{node.arg!r} — a facility owns no migration lineage"
                )
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id in _LINEAGE_DECLARATIONS
                    ):
                        problems.append(
                            f"{name}: {relative}:{node.lineno} assigns "
                            f"{target.id!r} — a facility owns no migration lineage"
                        )
            if isinstance(node, ast.Compare):
                for comparator in node.comparators:
                    if (
                        isinstance(comparator, ast.Constant)
                        and comparator.value in _PRODUCT_IDENTITIES
                    ):
                        problems.append(
                            f"{name}: {relative}:{node.lineno} branches on product "
                            f"identity {comparator.value!r}. Variation enters "
                            "through the typed descriptor, never a branch"
                        )

    pyproject = package_dir / "pyproject.toml"
    if not pyproject.is_file():
        problems.append(f"{name}: has no pyproject.toml")
    else:
        declared = (
            tomllib.loads(pyproject.read_text(encoding="utf-8"))
            .get("tool", {})
            .get("poetry", {})
            .get("dependencies", {})
        )
        runtime = sorted(key for key in declared if key != "python")
        if runtime:
            problems.append(
                f"{name}: declares runtime dependencies {runtime}. A facility a "
                "build runner adopts must not make it adopt a runtime too"
            )
    return problems


# ── the real package ────────────────────────────────────────────────────────


def test_the_deployment_foundation_is_a_stateless_facility() -> None:
    assert facility_violations(PACKAGE_DIR) == []


def test_the_dossier_classifies_it_as_a_universal_facility() -> None:
    """The checker above is keyed to the word, so the word has to be there.

    Without this, renaming the classification would silently take the package
    out of scope of every rule in this file while every test still passed.
    """
    dossier = tomllib.loads(
        (PACKAGE_DIR / "EXTRACTION.toml").read_text(encoding="utf-8")
    )
    assert dossier["classification"] == "universal-facility"
    assert dossier["package"] == "dotmac-deployment-foundation"


def test_no_module_manifest_is_REFERENCED_even_though_the_docs_name_it() -> None:
    """The AST check, stated separately so a red build names the property.

    This deliberately does NOT grep for the string. `__init__.py`'s own
    docstring says "No `ModuleManifest`, no models, no migrations" — explaining
    the boundary is most of what makes it survivable — and an earlier version of
    this test failed on that sentence. That is the exact failure this file's
    docstring warns about one level up, and it is worth having made once.
    """
    problems = [
        problem
        for problem in facility_violations(PACKAGE_DIR)
        if "ModuleManifest" in problem
    ]
    assert problems == []
    assert "ModuleManifest" in (
        PACKAGE_DIR / "src/dotmac_deployment_foundation/__init__.py"
    ).read_text(
        encoding="utf-8"
    ), "the docstring that once broke this test should still be there"


# ── sensitivity proofs (ADR-0018) ───────────────────────────────────────────
#
# A checker run over a clean tree passes for the wrong reason. Each test below
# plants exactly one violation and requires the checker to find it, and the
# first test is the negative control without which every other assertion here
# could pass because the checker rejects everything.


def _synthetic(tmp_path: Path, *, body: str = "def go() -> None: ...\n") -> Path:
    package = tmp_path / "dotmac-fake-facility"
    src = package / "src" / "dotmac_fake_facility"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text(body, encoding="utf-8")
    (package / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "dotmac-fake-facility"\nversion = "0.0.1"\n'
        '\n[tool.poetry.dependencies]\npython = ">=3.11,<3.14"\n',
        encoding="utf-8",
    )
    return package


def test_a_conforming_synthetic_facility_has_no_violations(tmp_path: Path) -> None:
    """The negative control.

    Without it, every "is caught" test below would pass on a checker that
    returned a problem for literally any input — which is the most common way a
    guard stops being one.
    """
    assert facility_violations(_synthetic(tmp_path)) == []


@pytest.mark.parametrize(
    ("body", "expected_fragment"),
    [
        ("from sqlalchemy import Column\n", "sqlalchemy"),
        ("import alembic\n", "alembic"),
        ("from fastapi import FastAPI\n", "fastapi"),
        ("import jinja2\n", "jinja2"),
        ("import yaml\n", "yaml"),
        ("from dotmac_kernel.db import get_db\n", "dotmac_kernel"),
        ("import httpx\n", "httpx"),
    ],
)
def test_a_planted_forbidden_import_is_caught(
    tmp_path: Path, body: str, expected_fragment: str
) -> None:
    problems = facility_violations(_synthetic(tmp_path, body=body))
    assert any(expected_fragment in problem for problem in problems), problems


def test_a_planted_module_manifest_is_caught(tmp_path: Path) -> None:
    problems = facility_violations(
        _synthetic(tmp_path, body="manifest = ModuleManifest(code='x')\n")
    )
    assert any("ModuleManifest" in problem for problem in problems), problems


@pytest.mark.parametrize("declaration", ["short_code", "migration_prefix"])
def test_a_planted_lineage_declaration_is_caught(
    tmp_path: Path, declaration: str
) -> None:
    problems = facility_violations(_synthetic(tmp_path, body=f"{declaration} = 'df'\n"))
    assert any(declaration in problem for problem in problems), problems


def test_a_planted_lineage_keyword_is_caught(tmp_path: Path) -> None:
    problems = facility_violations(
        _synthetic(tmp_path, body="thing = make(short_code='df')\n")
    )
    assert any("short_code" in problem for problem in problems), problems


def test_a_planted_product_branch_is_caught(tmp_path: Path) -> None:
    body = (
        "def go(product: str) -> int:\n"
        "    if product == 'erp':\n"
        "        return 1\n"
        "    return 0\n"
    )
    problems = facility_violations(_synthetic(tmp_path, body=body))
    assert any("product identity" in problem for problem in problems), problems


def test_a_planted_runtime_dependency_is_caught(tmp_path: Path) -> None:
    package = _synthetic(tmp_path)
    (package / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "dotmac-fake-facility"\nversion = "0.0.1"\n'
        '\n[tool.poetry.dependencies]\npython = ">=3.11,<3.14"\n'
        'pyyaml = ">=6.0"\n',
        encoding="utf-8",
    )
    problems = facility_violations(package)
    assert any("runtime dependencies" in problem for problem in problems), problems


def test_a_planted_migrations_directory_is_caught(tmp_path: Path) -> None:
    package = _synthetic(tmp_path)
    (package / "src" / "dotmac_fake_facility" / "migrations").mkdir()
    problems = facility_violations(package)
    assert any("migrations/" in problem for problem in problems), problems


def test_prose_explaining_the_absence_does_not_trip_the_rule(tmp_path: Path) -> None:
    """The guard reads code, not documentation.

    This package's own docstrings say "no ModuleManifest", "declares no
    short_code" and "imports no sqlalchemy" repeatedly, because explaining the
    boundary is most of what makes it survivable. A substring scan would fail
    the very file that documents the rule — which is exactly how the Workspace
    `.dmui-*` guard failed CI on a comment.
    """
    body = (
        '"""This package declares no short_code and no migration_prefix.\n\n'
        "It never imports sqlalchemy and holds no ModuleManifest, and it does\n"
        "not branch on whether product == 'erp'.\n"
        '"""\n\n'
        "# short_code: deliberately absent; see the docstring above.\n"
        "def go() -> None: ...\n"
    )
    assert facility_violations(_synthetic(tmp_path, body=body)) == []
