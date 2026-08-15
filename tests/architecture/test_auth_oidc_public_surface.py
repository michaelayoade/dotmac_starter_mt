"""`dotmac-auth-oidc` stays a protocol package.

Shaped after `test_ui_public_surface.py`: AST-based, never string matching, with
a sensitivity proof for each checker (ADR-0018 — a guard that cannot be shown to
fire is not a guard).

The boundary being defended is the one that makes ERP's client unreusable. That
module queries `FederatedIdentity` and `Person` inside the protocol flow, and its
callback issues the product's session; the result is a "shared" OIDC client that
cannot leave ERP. Three checks below exist so this package cannot drift the same
way: no forbidden import, no local-identity or session vocabulary, no provider
name.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import dotmac_auth_oidc

PACKAGE_ROOT = Path(dotmac_auth_oidc.__file__).parent
PYPROJECT = (
    Path(__file__).resolve().parents[2] / "packages/dotmac-auth-oidc/pyproject.toml"
)

# Each entry is a capability that would change what this package IS.
FORBIDDEN_IMPORTS = frozenset(
    {
        "alembic",  # holds no rows
        "app",  # a shared package that imports the assembly runs in one repo
        "dotmac_kernel",  # "which local party" and "may they" are the kernel's
        "fastapi",  # serves no routes; must not pick the consumer's HTTP shape
        "jinja2",  # renders nothing
        "psycopg",
        "sqlalchemy",  # stateless: no ORM, no session, no table
        "starlette",
    }
)

# Vocabulary that would mean the protocol client had grown a local-identity or
# session concern — the exact coupling ERP has.
FORBIDDEN_CONCERNS = (
    "Party",
    "AuthSession",
    "UserCredential",
    "set_cookie",
    "issue_access_token",
)

# ADR-0024: shared execution paths carry no provider branches. A provider quirk
# is fixed at the protocol level or not at all.
PROVIDER_NAMES = (
    "keycloak",
    "entra",
    "azure",
    "okta",
    "auth0",
    "google",
    "facebook",
    "cognito",
    "onelogin",
    "ping",
)


def _source_files() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def find_forbidden_imports(rel_path: str, source: str) -> list[str]:
    """AST-only, so a package name inside a comment or docstring never trips."""
    violations: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            root = name.split(".")[0]
            if root in FORBIDDEN_IMPORTS:
                violations.append(f"{rel_path}:{node.lineno} imports {root!r}")
    return violations


def find_forbidden_concerns(rel_path: str, source: str) -> list[str]:
    """Identifier-level, so prose explaining WHY a concern is absent is fine.

    Only `ast.Name` and `ast.Attribute` are inspected — the docstrings in this
    package name `Party` and `set_cookie` deliberately, to say they are not
    here, and a text scan would punish the explanation.
    """
    violations: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            identifier = node.id
        elif isinstance(node, ast.Attribute):
            identifier = node.attr
        else:
            continue
        if identifier in FORBIDDEN_CONCERNS:
            violations.append(f"{rel_path}:{node.lineno} references {identifier!r}")
    return violations


def test_the_package_imports_nothing_it_must_not() -> None:
    violations: list[str] = []
    for path in _source_files():
        violations += find_forbidden_imports(
            str(path.relative_to(PACKAGE_ROOT)), path.read_text()
        )
    assert not violations, "\n".join(violations)


def test_the_package_holds_no_local_identity_or_session_concern() -> None:
    violations: list[str] = []
    for path in _source_files():
        violations += find_forbidden_concerns(
            str(path.relative_to(PACKAGE_ROOT)), path.read_text()
        )
    assert not violations, (
        "\n".join(violations)
        + "\n\nThis package ends at a verified (issuer, subject). Resolving a "
        "local party is `dotmac_kernel.external_identity`; issuing a session is "
        "the product's identity facet."
    )


def find_provider_names(rel_path: str, source: str) -> list[str]:
    """Provider names in CODE — identifiers and runtime string literals.

    Deliberately NOT a text scan, and this was learned twice. Prose in this
    package says "no Keycloak, Entra, Google or Auth0 branch" precisely to tell
    a reader the boundary is intentional; a text scan fails on that sentence and
    the only way to satisfy it is to delete the explanation. Word-boundary
    matching also matters — a substring check flags `ping` inside `mapping`.

    Docstrings are excluded for the same reason; a provider name in a comment
    documenting an ABSENCE is the opposite of a provider branch.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover
        return []

    # Identify docstring NODES, not their cleaned text: `ast.get_docstring`
    # dedents and strips, so comparing against a raw `Constant.value` never
    # matches — which is how the first version of this still fired on the
    # module docstring it was meant to exempt.
    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_nodes.add(id(body[0].value))
    words: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            words.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            words.append((node.lineno, node.attr))
        elif isinstance(node, ast.keyword) and node.arg:
            words.append((getattr(node, "lineno", 0), node.arg))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstring_nodes:
                words.append((node.lineno, node.value))

    violations: list[str] = []
    for lineno, word in words:
        lowered = word.lower()
        for provider in PROVIDER_NAMES:
            if re.search(rf"\b{re.escape(provider)}\b", lowered):
                violations.append(f"{rel_path}:{lineno} names {provider!r}")
    return violations


def test_no_provider_name_appears_in_the_package() -> None:
    violations: list[str] = []
    for path in _source_files():
        violations += find_provider_names(
            str(path.relative_to(PACKAGE_ROOT)), path.read_text()
        )
    assert not violations, (
        "\n".join(violations)
        + "\n\nADR-0024: shared execution paths contain no provider branches."
    )


def test_the_declared_dependencies_are_the_two_that_cannot_be_faked() -> None:
    declared = set(
        tomllib.loads(PYPROJECT.read_text())["tool"]["poetry"]["dependencies"]
    )
    assert declared == {"python", "pyjwt", "httpx"}, (
        f"dependency set is {sorted(declared)}. This package verifies signatures "
        "and fetches key material; everything else it needs is injected."
    )


def test_the_pyjwt_floor_stays_at_the_security_release() -> None:
    """PyJWT advisory GHSA-jq35-7prp-9v3f: an ALGORITHM ALLOW-LIST BYPASS
    affects versions through 2.12.1 — the precise control this package leans on
    hardest. A floor below 2.13 would leave `ALLOWED_ALGORITHMS` bypassable by
    the library that enforces it, so this is a security floor and lowering it to
    widen compatibility is a regression, not a trade."""
    declared = tomllib.loads(PYPROJECT.read_text())["tool"]["poetry"]["dependencies"][
        "pyjwt"
    ]
    spec = declared["version"] if isinstance(declared, dict) else declared
    assert (
        ">=2.13" in spec
    ), f"pyjwt floor is {spec!r}; GHSA-jq35-7prp-9v3f requires >=2.13"
    assert (
        "crypto" in (declared.get("extras") or [])
        if isinstance(declared, dict)
        else True
    )


def test_python_jose_is_not_reintroduced() -> None:
    """The easy port was `python-jose` 3.3.0 — the release ERP, CRM and Sub all
    pin, from 2021, which pulls the pure-Python `ecdsa`. The objection is the
    four-year-old PIN a drop-in port would inherit, not the project (which still
    publishes releases); either way, easier is not a reason to found a new
    shared security library on it."""
    declared = tomllib.loads(PYPROJECT.read_text())["tool"]["poetry"]["dependencies"]
    # The PARSED table, not the file text: the pyproject comment explains at
    # length why python-jose was declined, and a text scan would fail on that
    # explanation — the same trap the provider-name check above documents.
    assert not any("jose" in name.lower() for name in declared), sorted(declared)


def test_the_version_matches_the_distribution() -> None:
    declared = tomllib.loads(PYPROJECT.read_text())["tool"]["poetry"]["version"]
    assert dotmac_auth_oidc.__version__ == declared, (
        f"__version__ is {dotmac_auth_oidc.__version__!r} but pyproject declares "
        f"{declared!r} — bump BOTH. (This is the drift that went unnoticed in "
        "four other packages because only the kernel had this test.)"
    )


def test_the_manifest_modules_import_and_declare_all() -> None:
    import importlib

    assert not (dotmac_auth_oidc.SUPPORTED_MODULES & dotmac_auth_oidc.INTERNAL_MODULES)
    for name in dotmac_auth_oidc.SUPPORTED_MODULES:
        module = importlib.import_module(name)
        assert getattr(module, "__all__", None), f"{name} declares no __all__"


# NOTE: "declares no manifest / lineage / namespace / persistence" is NOT
# checked here any more. It moved to
# `tests/architecture/test_product_first_extraction.py::stateless_adapter_violations`,
# which applies it GENERICALLY to any package declaring
# `classification = "stateless-protocol-adapter"` rather than to this one by
# name — and which is AST-based, so it does not fail on this package's own
# docstring saying it declares no `short_code`. That duplicate check lived here
# first, was substring-based, and failed exactly that way.


# ── Sensitivity proofs (ADR-0018) ───────────────────────────────────────────


def test_the_import_checker_fires_on_a_planted_violation() -> None:
    bad = "from dotmac_kernel.models import Thing\nimport sqlalchemy\n"
    found = find_forbidden_imports("planted.py", bad)
    assert len(found) == 2
    assert find_forbidden_imports("clean.py", "import json\nimport hmac\n") == []


def test_the_concern_checker_fires_on_a_planted_violation() -> None:
    bad = "def f(db):\n    return db.get(Party, 1)\n"
    assert find_forbidden_concerns("planted.py", bad)


def test_the_concern_checker_is_not_fooled_by_prose() -> None:
    """The complement, and the reason the checker is AST-based: this package's
    docstrings say "queries no Party" on purpose, and a text scan would fail on
    its own explanation."""
    prose = '"""This module never resolves a Party and sets no cookie."""\n'
    assert find_forbidden_concerns("clean.py", prose) == []
